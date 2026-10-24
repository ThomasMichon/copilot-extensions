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
| Updater failure-boundary implementer | Only the two reached stdio hops and native-owner settlement of positively confirmed pre-spawn failure under #7743; no shared broker or installed recovery authority | Managed source handle `rusty-kiln`; public coordination on #7743 |

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
- [x] Land the Python-owned aggregate and nested-output formatter, declared
  native encoding boundary and release-materialization contract (#6869).
- [x] Implement the shared formatter extension for aggregate update,
  captured plugin installers, nested output and native maintenance's
  persistent/status sinks (#6869 and #6904).
- [x] Route synchronous bootstrap and blocking marketplace installer output
  through the native timing, outcome and persistent-log adapters (#6920).
- [x] Frame standalone Worktrees install/update output in the existing
  admitting parent, retaining child-exit timing, native arguments, source
  admission and lease/containment ownership (#7119).
- [x] Frame finite post-maintenance Bash preflight log scopes without
  extending renderer lifetime across the Picker or interactive session
  (#7672).
- [x] Persist detached Windows marketplace-installer output through a
  launch-owned observer without changing the installer's native authority
  (#7683).
- [x] Frame detached Bash marketplace-installer output through its launch-owned
  observer, retaining the native invocation and existing log destination
  (#7697).
- [x] Replace routine raw-command echoes with passthrough/pane/direct argument counts
  and existing timed action labels, preserving native argument values (#7708).
- [x] Make early/late Bash fallback diagnostics UTC-correlated and explicit
  about unavailable elapsed timing outside finite renderer scopes (#7727).
- [ ] Finish the remaining standalone installer output boundaries through
  their actual output owners.
- [x] Admit Containers' actual PowerShell `init.ps1` producer and preserve its
  optional Docker preflight diagnostics and failure status on both platforms
  (#7854).
- [x] Qualify MCP's actual PowerShell `init.ps1` producer and its remaining
  visible native diagnostic boundaries without formatting MCP protocol output
  (#7879).
- [x] Qualify Budget Guidance's actual self-staged PowerShell installer and
  its construction-shell diagnostics before declaring captured UTF-8 output
  (#7888).
- [x] Qualify Machines' actual nested PowerShell/Python producers, including
  legacy decision progress and undecorated lifecycle JSON (#7901).
- [x] Frame the fresh Manager bootstrap's own explicit human messages without
  requiring an installed runtime or duplicating PowerShell information records
  (#8020); native child diagnostics and exceptions remain separate boundaries.
- [ ] Prefix every human-readable install/update line with a wall-clock time;
  suffix it with honestly labeled monotonic elapsed time or completed-step
  duration. Do not represent arbitrary child-line arrival as execution cost.
- [x] Normalize known legacy native progress markers to the shared human state
  vocabulary without changing owner states, completion or structured outcomes
  (#7736).
- [x] Restore the registered payload-refresh phase after marketplace
  authorization without changing auth, acquisition or outcome semantics (#7801).
- [x] Retain numeric payload failure classes before shortening diagnostics,
  including advisory attempts and final contention/I/O outcomes (#7828).
- [ ] Consolidate banners, status names, nesting and error presentation;
  preserve useful detail without duplicate timestamps, repeated status
  prefixes or contradictory aggregate success.
- [x] Replace remaining installer-owned stamp, initialization and provisioning
  decoration through the existing emitters, retaining deferral policy and
  admitting-parent outcomes (#7908).
- [ ] Preserve JSON/quiet/protocol output, redaction and Windows/POSIX parity.

### Bounded updater failure-boundary repair

This independent, explicitly transferred slice preserves the coordinator's
existing #6812 single-pass repair and the #8370 persistent installer logging
owner. The [bounded plan](updater-failure-boundaries.md) closes existing
Installer & Configurator observability and failure-accounting intent; it does
not replace the held #6859 / #7833 lifetime repair.

- [ ] Preserve both reached no-window output channels through their actual
  subprocess owners, with interactive passthrough unchanged.
- [ ] Settle only a positively confirmed no-launch failure through native
  ownership and fresh predecessor/routing proof; retain uncertain custody and
  the primary exception.
- [ ] Qualify the actual native consumer boundaries, obtain source review and
  normal containing release, and report installed acceptance separately.

### Phase 3 - Remove proven unnecessary work

- [x] Verify existing final-integration deferral with native invocation counts
  and current/changed/first-touch aggregate ordering (#6933).
- [x] Verify Codespaces source-level admitted reuse and health/content/schema
  refusals through real native construction adapters (#6940).
- [x] Remove PowerShell UV acquisition from admitted healthy reuse while
  preserving fresh-construction acquisition and cleanup (#7054).
- [x] Reconcile Bridge serving-version drift through the admitted runtime's
  existing cutover path rather than returning a misleading local-content
  no-op or rebuilding an already current slot (#6826, #6877); installed
  serving-state proof remains in the rollout validation below.
- [ ] Verify #6598's existing Codespaces admitted-construction reuse after
  promotion instead of reimplementing it; attribute any remaining
  package/completion/activation failure with the new phase outcomes.
- [x] Avoid unused site-package initialization in isolated portable
  installation-mode probes while retaining fresh payload-owned authority
  and complete real-consumer decision parity (#7818).
- [x] Avoid the extra PowerShell hop in selected-context validation while
  retaining fresh payload-owned checks, native compatibility and UTF-8 root
  decoding (#7919).
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

- [x] Use deterministic clocks to cover start, progress, completion, no-op,
  failure, nesting, multiline output, empty output and clock movement.
- [x] Prove clean JSON/stdout and diagnostic stderr at real subprocess
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

### 2026-10-23 - Qualify current consumers with temporary CPU allowances

- The operator explicitly authorized temporarily increasing validation
  allowances for congested CPU. Private validation runners used 60 seconds
  per item and 600 seconds per cohort; the new project-only native callback
  received 40 seconds instead of 20. Production deadlines, required CI,
  inputs, assertions, custody and publication policy were unchanged.
  These results are not original-limit performance qualification.
- Current Windows consumer coverage passed 175 cases: all 45 repair cases,
  41 scoped controls, 45 bootstrap/stream/snapshot cases, and 44 interpreter,
  native project-only callback and governance controls. The complete repair
  cohort took 519.31 seconds; its single-update call took 22.38 seconds.
- Current native Linux coverage passed 126 cases with 33 platform-specific
  exclusions. A failed first run exposed admission tests selecting Windows
  PowerShell from a POSIX interoperability PATH. Restricted that selector
  to native hosts, matching the other native test modules, and added a
  platform-selection regression. All 27 native Windows admission controls
  still pass; Linux no longer invokes a foreign-platform interpreter.
- The preceding full staging/contract/generation matrix remains recorded
  against its actual source. Current consumer qualification removes the
  immediate source-publication blocker under the authorized temporary
  allowances, not the remaining normal-load, installed, serving or parent
  objective gates. Review and required CI still precede any source merge;
  no further installed update is authorized.

### 2026-10-23 - Keep publication blocked after the latest source integration

- A second pre-publication synchronization retained #7667's original
  project-root propagation and extracted governance owner, without replacing
  either with the finite receipt/status transport. The earlier complete
  qualification below belongs to the preceding integrated source, not this
  newer candidate. The publication attempt did not open a PR.
- Current Windows qualification is blocked by the original 30-second item
  limit: the scoped management-repair case and the newly integrated native
  project-only publication callback exceeded it. Interpreter-admission
  controls completed before the callback timeout; subsequent controls were
  not reached. No deadline, assertion or required input was waived.
- Copied-script diagnostics show the scoped path reaches its intended
  construction fence. Separate diagnostic calls completed in 18.15 and
  23.91 seconds, but instrumented passes do not qualify the candidate.
  The publication trace reaches its final canonical completion check after
  initial admission, both source checks and the complete copy inventory;
  it is not evidence that completion succeeded.
- Aggregate host observation found sustained CPU pressure: twelve samples
  over two minutes ranged from 54 to 100 percent, with eleven at least
  90 percent. No idle validation window was observed. This is a measured
  environmental blocker, not proof that every timeout has that sole cause.
  Do not repeat unchanged gates, suppress checks or change another
  session's processes to obtain a passing result.
- Review/publication remains blocked pending a qualified current-source
  validation window or a justified further source fix. Installed rollout,
  serving/no-op measurements and the parent objectives remain open under
  their existing owners and permissions.

### 2026-10-23 - Qualify the integrated source without deadline exceptions

- Synchronized the owned candidate with current `dev`, retaining the
  accepted #8289 stream implementation and the upstream installer,
  dependency, source-registration and serving-monitor changes. Only two
  generated output declarations conflicted; regenerated them from the
  combined source. Local backup refs retain the pre-integration history.
- The integrated Windows repair suite passed all 45 cases in complementary
  acquisition/ownership and execution/opt-in cohorts. Full bootstrap and
  upstream stream coverage passed 38 cases, the complete scoped cohort
  passed 41, and snapshot plus interpreter-admission coverage passed 33.
  Native staging/CWD/watchdog coverage passed 35 with its POSIX-only case
  excluded. Deterministic contract/generation coverage passed 261 with
  matching-platform exclusions.
- The same source on native Linux passed 110 repair/bootstrap/scoped/
  snapshot cases, with Windows-only cases excluded. Native staging and
  deterministic contract/generation coverage passed another 286, with
  matching-platform exclusions. Both platform runs retained the original
  30-second item and 300-second group limits.
- Oversized combined packets reached the group limit without an assertion
  failure. Re-selected independent module/behavior cohorts rather than
  increasing the limit; every resulting complete cohort passed. No failed
  item, assertion, required input or admission check was omitted.
- Documentation consistency and the contribution-scoped module-size guard
  pass. The identifier scanner reports no configured forbidden-identifier
  list, so its invocation is not claimed as a privacy scan; manually
  reviewed the diff for neutral fixtures and absence of private identifiers.
- The source qualification block is resolved and the candidate is ready
  for the repository's required review/CI flow. This does not resolve the
  remaining installed/serving, whole-host no-op, external-venue or parent
  objective gates, and authorizes no further host update.

### 2026-10-23 - Converge on the accepted repair-stream fix

- Source synchronization found the independently merged #8289 repair-child
  stream forwarding. Its settlement loop and caller-selected standard
  streams remain the implementation to reuse, not supersede.
- Added a real-process control where Python selects file-backed input,
  output and diagnostic streams without replacing native descriptors
  zero/one/two. The local numeric-descriptor implementation failed both
  exit-zero and exit-17 controls, leaking output to the unselected channel.
- Adopted the accepted caller-selected stream forwarding and retained
  settlement/interruption behavior. Both new cases and the existing real
  pipe/NUL and custody controls passed: eight cases in 3.53s. This removes
  the substantive stream overlap before synchronizing the complete source
  candidate; it does not authorize an installed update.

### 2026-10-23 - Remove the duplicated lifecycle and qualify finite context reads

- Identified a structural missing-bin repair cost: provision and update both
  enter native admission, staging and the runtime handler. The separate
  provision pass existed because ordinary update refuses a missing bin.
  The operator approved changing this contract to one admitted full update,
  not relaxing its deadline or substituting an accepted validation gap.
- Manager now requests a private missing-runtime opt-in only for its
  captured legacy installation. The native update requires its explicit
  root and existing lifecycle ticket, retains canonical ownership and
  publication-policy checks and the Git prerequisite, refuses a substituted
  file/link, creates the missing ordinary bin and continues through the
  unchanged full update body. Ordinary update without the opt-in remains
  refusing. No new lease, authority or installed repair path was added.
- The Windows single-pass/opt-in selection passed 11 native cases in
  125.29s, including both PowerShell hosts, unadmitted/forged tickets,
  wrong action/context, absent root, unchanged ordinary-update refusal and
  bin-file substitution. The complete single-pass call took 14.68s. A
  subsequent coupled Git-prerequisite and routing/postcondition selection
  passed seven cases; that single-pass call took 12.88s.
- Added a finite payload-local transport for Windows' consecutive initial
  receipt and status reads. It invokes the same two SDK CLI actions with
  their original arguments in one interpreter, preserves their raw
  responses and SDK errors, and stops on first failure. Every original
  root/status/generation comparison remains, as do fresh publication and
  later revalidation calls. No receipt/path cache or SDK authority
  algorithm changed; Python-free/native fallbacks remain available.
- The 24 reader/parser controls passed, including real PowerShell 5.1/7
  UTF-8, no-site, two-action and exact-failure transport. The complete
  current Windows scoped cohort then passed all 41 cases in 234.85s under
  the original 30-second item and 300-second group limits. Its slowest
  native-policy call was 22.32s.
- These are source-level blocker fixes, not an installed rollout or effort
  completion. Final source synchronization, the remaining native/platform
  matrix, publication/review/release and the authorized installed proof
  remain open. The existing #7743 owner retains its independent Manager
  admission and installed-recovery work.

### 2026-10-23 - Correct payload startup and repair-fixture isolation

- Added failing real-consumer controls for site initialization in both
  Windows PowerShell hosts and the explicit Bash context reader. All five
  failed before the correction. Payload-owned admission, version probes
  and canonical context queries now use no-site isolated startup; installed
  runtime package loading, construction interpreter selection, validation
  calls and custody boundaries remain unchanged.
- The complete admission/reader selection passed 39 Windows cases,
  retaining older-interpreter discovery, explicit refusal, native fallback,
  Unicode arguments and exact child failures. The containing Manager
  repair CLI uses the same payload-only startup, without forwarding a new
  legacy bootstrap-interpreter hint.
- Reworked only Windows fixture removal into a joined four-worker native
  Python child. Its original 10-second child bound and 30/300-second
  qualification bounds remain. Its working and temporary directories stay
  outside the tree being removed; link targets and read-only-file failures
  remain protected and observable. Production cleanup is unchanged.
- Once cleanup allowed a full cohort to finish, six cases exposed stale
  temporary-directory state: acquisition attempted to use the first
  already-deleted fixture's temporary directory. Two ordered regressions
  failed before explicitly binding Python's cached temporary directory to
  each repair fixture. The cache is now restored by the owning monkeypatch
  scope. The containing bootstrap/fixture selection passed 36 cases.
- A complete corrected Windows scoped cohort is still not accepted: a
  subsequent run reached the original item limit in native construction.
  The ordinary legacy two-cycle case also remains blocked. A passing
  individual scoped case (28.78s including cleanup) is not substituted for
  either complete gate, and these fixture/source changes are not evidence
  of an installed update or healthy host no-op.

### 2026-10-23 - Map nested publication work before changing authority checks

- Traced the diagnostic profile through Worktrees' actual `Governance`
  consumer and the canonical deployment/publication SDK. The three custody
  borrows are initial publication validation, held admission and publication
  revalidation; each intentionally checks identity on both sides of the
  lock-owner observations and confirms native process birth afterward.
- Recorded the nested receipt, namespace, mode, policy and provenance work
  in the [audit](audit.md). Internal receipt/path call counts are not
  independent authority boundaries, and the existing scoped digest cache
  does not authorize path, receipt, policy or custody caching.
- No shared-SDK algorithm or production behavior changed. Foundation owners
  retain that work; the original Windows two-cycle and current full scoped
  gates, source-publication block and installed holds remain in force.
- Rejected a separate compiler-based staging experiment: on the complete
  fixture-selected 684-file input, four-worker copying took 0.661s but cold
  compilation added 0.728s; their combined 1.389s did not beat the retained
  helper's 1.329s comparison. Bytes, file times and modes matched, but this
  ordinary-tree microbenchmark is not link/provider or native acceptance.
  No compiler, assembly cache or new copy path was added to the installer.

### 2026-10-23 - Accepted capture source retains the bounded legacy discrepancy

- Synchronized the accepted legacy-owner capture change from #8010 while
  preserving the local presentation, copy and context-reader corrections.
  The actual squash merge is included; its feature-head ancestry is not
  a valid substitute for that check. This is source synchronization, not
  an installed rollout or original-recovery result.
- The unchanged Windows two-cycle gate still fails on that accepted source.
  Passive measurements reached a 16.563s first native cycle after 5.272s
  fixture setup; the second cycle exceeded the original item budget.
- Sparse copied-script markers separated admission, full staging copy,
  staged-child startup, signed-base selection and the construction fence.
  One diagnostic completed both construction cycles before timing out in
  fixture cleanup; another reached only the second child. These markers
  are diagnostic, not equivalent performance or acceptance evidence.
  The observed variation spans several phases, not just output capture.
  Neither an inherited-handle hang nor dominant stale-stage cleanup was
  established, and no policy, signer or cleanup change was inferred.
- A complete 685-file comparison found directory-info enumeration slower
  than the retained native copier (2.907s versus 2.500s); discarded it.
  A separate complete fixture-copy comparison reduced Windows copying
  from 1.968s to 0.828s using four bounded native-copy workers, with equal
  bytes, file times and modes. Fixture preparation retains the full tree,
  links, ignore and overwrite behavior, independent files and surfaced
  worker errors; it joins before directory metadata restoration or use.
- The four targeted fixture-copy controls pass, but the native two-cycle
  gate still fails after that setup-only improvement. It is not claimed
  as a fix for host-update performance or permission to publish. Original
  deadlines, native arguments, admission checks and installed holds remain.
- The synchronized Linux repair, scoped-policy and staging selection passed
  70 cases with 25 platform skips in 177.60s. The post-sync Windows scoped
  selection has two additional accepted historical-owner cases, so the
  earlier 28-case result is not final coverage of its current 30 cases.
- The new automatic-cell historical-snapshot case spent 14.500s in its
  fixture's ordinary `shutil.copytree`. Reusing the complete fixture copier
  reduced that operation to 0.719s; the unchanged policy case then passed
  in 24.34s including setup and cleanup. Snapshot bytes and provenance,
  source selection and all native checks remain real.
- A subsequent full Windows scoped run still reached its original item
  limit during an earlier automatic-cell case's fixture cleanup. The full
  current cohort is therefore not accepted. The independent current
  Windows staging/CWD/watchdog cohort passed 35 with one platform skip in
  160.44s. Coordination evidence is retained on #6812; #7743's later
  historical-helper admission and installed-recovery block is separate.
- All seven current Windows historical-snapshot cases passed in 137.90s;
  the final snapshot/automatic-snapshot selection passed eight cases on
  native Linux in 40.73s. These retain missing/corrupt snapshot refusal and
  execution exclusively from refreshed native source.
- Further scoped phase markers separate repeated canonical queries from
  staging and construction. A diagnostic profile of the real runtime's
  pre-construction work recorded 28 receipt validations, 1,657 canonical
  path calls and 3,812 native final-path queries in 2.043s of profiled work.
  These are nested call counts, not permission to drop 28 governance
  boundaries or reuse stale decisions. No receipt/path cache, combined
  authority algorithm or production cleanup change was added.
- Individual instrumented scoped controls can finish while the unchanged
  complete Windows gate still fails. They are diagnostic evidence only.
  Source publication, ordinary legacy two-cycle qualification and complete
  current scoped qualification remain blocked.

### 2026-10-22 - Keep publication blocked and qualify explicit context readers

- The operator explicitly retained the publication block rather than accepting
  native-validation gaps. No deadline, process limit or policy exception was
  introduced; this effort and its installed-completion gates remain open.
- A native Linux scoped-repair trace reached dependency-free shell validation
  and status reads before its original deadline. Manager already supplied an
  exact interpreter, but early POSIX reads ignored that existing hint.
  Honoring it routes the same arguments through the canonical Python SDK;
  callers without it still retain the Bash reader. All 20 scoped/routing
  cases then passed on native Linux; the former failing call took 10.10s.
- Windows structured staging independently stalled in a forced provider copy.
  Moved the same native copier before contextual staging, after required
  output initialization, in all 13 consumers. Its own byte-identity guard
  rejects a missing or divergent block. Worktrees' structured payload copy
  uses it without changing paths, canonical-library handling or watchdog.
- An explicitly supplied Windows interpreter also now drives canonical
  context reads through the existing hidden-process adapter. The original
  validation, status, publication and later governance checks remain, with
  identical argument values, exit checks, identity and generation assertions.
  Native PowerShell remains the unsupplied-interpreter fallback.
- PowerShell 5.1/7 controls retain every mapped field, literal/Unicode values,
  isolated execution, native fallback and exact failure exit. They exposed
  an empty-output newline, which was fixed rather than weakening the stdout
  assertion. The first real Windows scoped case passed its original item
  limit: setup 8.18s, call 16.77s, cleanup 1.36s. The complete current
  Windows scoped-policy cohort subsequently passed all 28 cases in 196.04s,
  including automatic selection, deactivation, refusals and native transport.
  The current shared staging/CWD/watchdog cohort passed 35 cases with one
  platform skip in 136.09s. Both retained the original per-item/group limits.
- Fixture preparation runs only two independent copies concurrently, joins
  them before policy or damage mutations, and retains separate pristine
  installed/published trees, complete inputs and surfaced failures. This
  changes fixture setup cost, not claimed installed-update performance.
- The legacy provision/update two-cycle case still exceeds its original
  item limit. Passive instrumentation preserving native commands and capture
  measured 5.604s setup and a 15.156s first cycle before the second exceeded
  the remaining budget. Sparse debugger probes are diagnostic, not equivalent
  cost comparisons or full-lane acceptance.
- Extending bootstrap-interpreter forwarding to ordinary legacy native
  invocation did not clear its bounded native gate. That experiment and its
  four unit-only controls were reverted; `_invoke` and its normal native
  arguments are exactly back at the retained baseline. No signer-selection,
  native-wait, capture, custody or installed-state change was retained.
- Source publication, complete native-policy qualification and healthy
  installed/no-op acceptance remain blocked. No further host update,
  dependency installation, signature repair or active-effort finalization
  was performed.
- Read-only coordination confirmed that the existing owner's #8010 merged
  at `2026-10-23T04:41:09Z`. Preserve the qualified local source work and
  synchronize that accepted legacy-capture change before attributing more
  legacy-path failures to the retained older base. Its source merge is not
  evidence of an authorized installed update or successful original recovery.

### 2026-10-22 - Native self-stage copy cost isolated and reduced at source

- The remaining Windows policy timeout was not initial lifecycle admission:
  a private stack sample reached its native-child wait within one second.
  PowerShell tracing then reached the shared self-stage provider copy.
  At the unchanged outer item deadline, only 245 of 684 payload files had
  copied. This is boundary attribution, not a complete policy result.
- A bounded same-input comparison retained every file and used fresh private
  destinations. `Copy-Item` reached its 20-second command limit at 659 of
  684 files; the actual candidate native copier completed all 684 in 2.281
  seconds. File bytes, modification times and modes matched. The baseline
  duration is a lower bound, not an invented completed-run speedup.
- Reused the installer's established native-copy pattern in the shared
  PowerShell self-stage block, synchronized across all 13 consumers.
  Ordinary files use .NET copying; reparse points retain provider behavior.
  No input, staging destination, argument, deadline, admission check,
  watchdog, CWD relocation or owner/PID guard was removed.
- Native PowerShell 5.1 and 7 controls passed for complete trees, hidden and
  read-only inputs, empty directories, bytecode, literal/Unicode names,
  file and directory links, metadata and explicit I/O failures. The complete
  staging/CWD/watchdog fixture suite passed 35 cases with one platform skip.
- The actual marketplace-incumbent repair-policy case now completes under
  its original item limit: setup 9.57s, call 16.44s and cleanup 1.77s.
  Fixture cleanup uses native Windows deletion of its exact private root;
  controls prove linked targets survive and missing-root failure is retained.
- The complete optional Windows policy lane is still **not passing**: it
  reported 16 item passes, then the two-cycle provision/update case exceeded
  the original 30-second item limit. The three later cases passed separately.
  A diagnostic trace measured the first real cycle at 12.484s and reached
  the second native cycle before expiry; it is not acceptance of both cycles.
- The exact staged source passed the full repair-policy and staging-contract
  selection on native Linux: 39 passed and 17 platform-specific cases skipped.
  All 20 repair-policy cases passed there, including the two-cycle case.
  The 13-plugin install contract, generated native declarations and touched
  Python lint also passed. These results do not waive the Windows discrepancy.
- First-touch evidence uses real isolated native staging and installer policy
  through the existing construction fence, not a complete fresh-machine
  installation. A full provisioning or live external update was not run:
  current authority is source-only and the earlier update allowance is
  exhausted. Existing installed-completion owners and gates are unchanged.
- #7743/#8010 retain legacy capture, admission and rollout. #5820 retains
  version-slot relocation and whole-lifecycle/fallback redesign. This is a
  bounded copy-cost correction, not a competing repair or architectural
  self-staging rewrite. Source publication, the remaining native-policy
  discrepancy and installed healthy/no-op acceptance remain open. No host
  update, dependency installation or deadline increase occurred.

### 2026-10-22 - Fresh repair presentation and native descriptors qualified locally

- The source candidate opens a human scope only for the fresh Manager's
  private repair prefix, before `UpdateRun` exists. It reports its own
  missing-source and caught-refusal diagnostics and the exact repair exit,
  retaining ordinary-update and unscoped exit-only behavior.
- A real redirected Windows subprocess exposed an additional output loss:
  the existing repair `_wait` retained native exits zero and seventeen but
  lost both child output streams under `CREATE_NO_WINDOW`. Explicit existing
  descriptors zero, one and two restore forwarding without capturing,
  decorating or merging child streams. The wait/interrupt-settlement loop,
  native flags, arguments, environment and lifecycle admission are unchanged.
- The focused cohort passed 88 cases on both Windows and Linux. Native
  controls cover UTF-8 pipe input, DEVNULL, raw JSON stdout, diagnostic
  stderr and distinct success/failure exits; deterministic controls cover
  framing, multiline redaction, errors, missing formatter and scope cleanup.
- Additional optional repair-policy qualification is **not passing**:
  Windows reached the original item limit first in fixture copying, then
  in its captured native child; Linux reached it in scoped construction.
  These cases replace `_wait` with their own capture adapter, so they are
  not evidence against or acceptance of the new descriptor forwarding.
  They remain unresolved rather than being relabeled as harmless or given
  larger deadlines.
- A same-input Windows fixture-copy probe completed all four source trees
  in 7.17 seconds using native metadata-preserving copying; Python copying
  exceeded the unchanged 30-second item budget. The candidate fixture
  correction preserves full inputs and has passing byte/metadata controls,
  but does not by itself resolve the native-policy timeout.
- Coordinated the non-overlapping descriptor and fixture work with #7743's
  existing #8010 owner. That PR retains legacy-owner capture and rollout;
  no competing ownership algorithm or installed repair was attempted.
- This candidate is not published or merged. Review, required CI, remaining
  native-policy diagnosis and installed acceptance remain open. No host
  update, dependency installation, deadline increase or active-effort
  finalization was performed.

### 2026-10-22 - Fresh bootstrap presentation merged; retain repair diagnostics

- Merged #8020 as `5de32636d6609c686a9f84594d1cc48bb3d9ef6c`
  after exact-head approval without findings and all required checks passing.
  Managed publication preserved the tested bootstrap, regression and native
  wait/stdio source bytes through rebase. Native PR status reconciled the
  actual merge and synchronization pulled the source forward.
- This closes only bootstrap-owned explicit human messages. It does not frame
  prerequisite subprocesses, thrown shell exceptions, or the fresh Manager's
  subsequent engine-repair diagnostics. The repair currently precedes
  `UpdateRun` construction, so its own failure output is outside the ordinary
  update formatter. Trace that source-owned boundary next without changing
  repair admission, native waits, source selection or structured results.
- #6812 remains open; this effort and parent #6600 remain Active. No host
  update or installed repair was performed, no additional rollout permission
  was inferred, and no active-effort worktree was finalized.

### 2026-10-22 - Source measurement journal merged; qualify bootstrap emitters

- Merged #8009 as `22fa78f06922d48a2bd91e1feb834b535b0defee`
  after exact-head approval without findings and all required checks passing.
  Native PR status reconciled the merge and native synchronization pulled
  this source worktree forward. The effort remains Active and was not
  finalized.
- Coordinated a bounded human-output follow-up with the existing #7743
  owner. The fresh Manager bootstrap's own progress, warnings and explicit
  diagnostics now use UTC-correlated stderr text. PowerShell uses an
  in-process monotonic stopwatch and avoids `Write-Host` information-record
  duplication; Bash explicitly reports unavailable elapsed timing before a
  portable provider exists.
- Native child output, exceptions, source/ref selection, prerequisites,
  ownership admission, construction, the #7987 wait/stdio repair and durable
  request custody are unchanged. This slice does not claim those separate
  producers are all framed and does not add an updater or reporting process.
- Combined Windows coverage passed 28 cases, with four POSIX-only skips.
  Both PowerShell hosts passed ordinary and encoded-command capture: one
  header on stderr, no duplicated host-information record, no human stdout,
  culture-invariant UTC/elapsed labels and retained failure exits. Native
  two-cycle controls retained UTF-8 streams, DEVNULL input, failure status
  and window/focus behavior. The explicit native-tier flag was set inside
  the isolated child environment; the earlier inherited-only attempt skipped
  those three opt-in cases and is not counted as their execution.
- Native Linux coverage passed 21 cases, with eleven unavailable-host/tier
  skips. Actual local-Git bootstrap fixtures retain success/failure exits,
  explicit malformed-repair diagnostics and physical-line framing. Installer
  synchronization, all thirteen install contracts, lint, documentation
  consistency and whitespace checks passed. Original limits were retained.
- Docker and devcontainer commands were discoverable only as unusable
  interop shims in the Linux venue: Docker integration was unavailable and
  the CLI could not execute its Node dependency. Bounded contained probes
  established that limitation after an initial direct discovery timed out.
  No tool or system repair was attempted; validation used the existing
  contained native lane. Full installation clean-room and external live
  rollout were not run for this producer-only slice; its fixtures stop at
  synthetic native commands and do not change construction or activation.
- No host update or installed repair was performed. Whole-flow, populated
  registry, healthy installed no-op and parent #6600 acceptance stay open.

### 2026-10-22 - Attribute real source CLI startup and context fan-out

- The actual CLI `main`, parsers, adapters and configuration discovery ran
  against disposable source-layout payloads and private synthetic state.
  Pre-launch took 3.846 seconds externally, including 1.343 seconds in the
  profiled CLI import; read-only reconciliation took 5.961 seconds, including
  1.112 seconds in import and 4.415 seconds in `build_plan`.
- Both returned clean JSON continue results without fallback reasons,
  diagnostics or stderr, and retained all fixture bytes and modification
  times. Only fixture-local Git reads and fresh read-only helper commands
  were admitted. These are not host updater, queue, construction or serving
  health measurements. Original test and child bounds were retained.
- The [audit](audit.md#source-cli-startup-and-explicit-context-planning)
  records the non-additive configuration profile and the populated-registry
  limitation. No broad cache or safety-gate removal is justified by these
  single synthetic samples.
- A complete explicit-context planner took 8.786 seconds with eleven fresh
  context resolves and eleven fresh legacy probes. It also retained an
  unchanged fixture and returned no actions or diagnostics. The old
  single-context shell-hop timing is not an equivalent whole-planner
  baseline and is not extrapolated into claimed savings.
- The existing #7743 owner merged its independently reproduced Windows
  final-handoff wait/stdio repair in #7987. Release and actual installed
  completion remain open; no further updater, installed modification or
  protected recovery was performed.
- Remaining source work includes the fresh bootstrap's actual human-output
  producer and any still-unqualified standalone boundary. Whole-command,
  populated-registry and healthy installed no-op proof remain open, as do
  this effort and parent #6600. Neither the measurements nor the other
  owner's merge grants another rollout attempt.

### 2026-10-22 - Complete synthetic planner cost isolated

- Measured the actual read-only `build_plan` with thirteen configured plugins,
  twelve intended targets after self-exclusion and eleven fresh cell-mode
  probes. Matching, changed and invalid-policy fixtures retained complete
  baseline/current result parity and all fixture bytes/mtimes.
- Current measured planner durations were 4.605, 4.253 and 4.598 seconds,
  respectively; required probes account for approximately 97-98%. There were
  zero update actions for matching versions, twelve for changed versions, and
  eleven refusals plus the one non-cell target for invalid policy.
- The [audit](audit.md#complete-synthetic-planner-without-an-explicit-context)
  records the paired baseline and sampling limits. The fixture exercises no
  explicit context, so the selected-context shell-hop change is not reached.
  These are not healthy installed no-op or whole-command savings.
- Preserved the original thirty-second item/300-second group limits after
  the first four-sample harness exhausted its bound in fixture snapshot work.
  Reduced sampling and parallelized only independent fixture preparation,
  without dropping files, scenarios, probe calls or refusal assertions.
- Next source boundary: attribute the actual pre-launch/CLI wrapper's
  startup, import and configuration-discovery cost in a contained fixture.
  Live verification remains blocked on #7743 and fresh explicit rollout
  authorization; no additional updater was invoked.

### 2026-10-22 - Construction presentation merged; planner measurement continues

- Merged #7996 as `0d64ec41b475cbad90f0113cd0cd2bc81f84c60a`
  after exact-head approval with no findings, no unresolved threads and every
  required check passing. The newer upstream bootstrap source was preserved;
  its generated installer declaration was refreshed after the backed sync.
- The combined source again passed the full output/materialization file on
  Windows (67 passed, one platform skip) and native Linux storage (62 passed,
  six unavailable-host skips). Source synchronization completed after merge.
- This closes the bounded construction-header slice, not the aggregate output,
  healthy no-op, release/installation or parent #6600 gates. No active-effort
  worktree was finalized and no host update was run.
- Next: measure the complete read-only planner against equivalent synthetic
  multi-plugin states, separating required probe counts and cost from the
  earlier selected-context consumer result. The deployed whole-flow gate stays
  blocked on the existing fresh-entry owner and new rollout authorization.

### 2026-10-22 - Worktrees and Codespaces construction output qualified

- Replaced the actual PowerShell and POSIX construction-action headers with
  ordinary progress-helper lines. Install, update, dev, stamp and provisioning
  remain distinct; Worktrees' dev-release and distinct-generation repair retain
  their qualifications. Codespaces' runtime-only install path identifies itself
  as provision. Shared status/uninstall emitters and lifecycle decisions are
  unchanged.
- Extended the existing batched source-emitter matrix. Real selected PowerShell
  install/update/provision branches run until a synthetic prerequisite refusal,
  retaining diagnostic output and exit 23 through the actual parent renderer.
  These fixtures do not invoke installation, service control or publication.
- The complete output/materialization regression file passed 67 Windows cases
  with one POSIX-only skip, and 62 cases on native Linux storage with six
  unavailable-PowerShell-host skips. Existing 30-second item and 300-second
  suite bounds were unchanged. Generated declarations, all thirteen plugin
  install contracts, touched Python lint and native PowerShell parsing passed.
- The first Linux attempt stopped before collection because its private
  temporary root no longer existed. Recreating that root with mode 0700 and
  checking its owner/type/mode restored the existing isolated lane; no test,
  timeout or source-admission condition was bypassed.
- The fresh-entry owner independently reproduced Windows process replacement
  losing the final result and is correcting its contained wait/exit boundary
  under #7743. That source repair, promotion and newly authorized installed
  acceptance remain separate from this presentation candidate. Both earlier
  update allowances remain exhausted; healthy no-op and equivalent whole-planner
  cost are still open.

### 2026-10-22 - Promoted runtime proof passed; fresh-entry verification refused

- One authorized normal Windows update completed with actual management EOF,
  exit zero and a `verified_current` run. It changed all thirteen runtime
  identities; the selected Worktrees, Bridge and Index versions were
  `1.24.194-dev1`, `0.9.111-dev1` and `0.10.63-dev1`. All thirteen selected,
  deployment and payload identities matched, retaining Containers' declared
  provider-exec suffix. Four Worktrees modules, including the selected-context
  and installation-probe changes, were byte-identical across reviewed source,
  installed payload and selected runtime. Independent Bridge and Index health
  reads confirmed the naturally activated versions after management EOF.
- The [operation audit](audit.md#promoted-update-and-fresh-entry-verification)
  records the complete phase cost: the Manager run took 2600.233 seconds,
  including 2017.657 seconds in the three runtime-update phases. This was
  changed-version construction, not an equivalent healthy no-op or a measured
  total-planner improvement. The already-running old orchestrator still
  attributed bulk payload refresh to its old authorization phase.
- The single authorized verification rerun instead reached the new fresh
  entry, advanced Manager to `0.6.78-dev1`, and refused engine repair because
  the legitimate schema-3 incumbent manifest had no `commandBinding`.
  Its durable request correctly persisted exit one, but the repository front
  door returned zero and the aggregate status still described the previous
  successful run. All thirteen plugin identities remained unchanged; the
  engine never reached its normal update phases. The shorter failed request
  is not healthy no-op evidence.
- [Exact sanitized evidence](https://github.com/ThomasMichon/copilot-extensions/issues/7743#issuecomment-6352516328)
  was sent to the existing fresh-entry owner under #7743, including the later
  engine ownership predicate and the separate native exit/wait discrepancy.
  That owner was already correcting legacy-schema admission. No competing
  bootstrap implementation, invented receipt, queue repair or additional
  update was performed. Both authorized invocations are exhausted; source
  correction, reviewed release and newly authorized installed acceptance
  remain separate gates. This effort and its parent remain Active.

### 2026-10-22 - Selected-context improvement merged; live proof still gated

- #7919 merged after a fresh current-head approval, no open findings or
  threads and all required checks passing. Upstream durable-launch changes
  rebased cleanly into separate executor methods; the four context-selection
  and planning function ASTs were unchanged. The rebased Windows planner plus
  real Unicode controls passed 166 cases with one platform skip; the exact
  published source's Linux planner passed all 165.
- The measured shell-hop reduction is source-qualified, not a claim that
  deployed failures, serving-state drift or a minute-long live reconciliation
  are solved. No additional host update or repair was run. Further live
  observation or rollout must respect the operator's explicit scope,
  verified promotion and existing ownership restrictions.

### 2026-10-22 - Portable selected-context consumer qualified

- The selected-context path now prefers the portable resolver in the same
  admitted payload, just as the installation-mode probe already does.
  Platform-native helper admission and native-only compatibility remain.
  Both routes retain fresh validation, exact context/plugin/durable-root
  arguments, same-plugin payload binding, cross-plugin expected-cell-root
  binding, the fifteen-second deadline and explicit refusals.
- The selected-context transport moved into the existing probe module,
  shrinking the grandfathered reconciler rather than widening its baseline.
  Its caller-owned subprocess is windowless. Both actual resolver producers
  declare UTF-8; explicit decoding also corrects the native fallback's
  locale-dependent non-ASCII root interpretation.
- Real own-plugin, cross-plugin, Unicode and tampered-namespace controls pass
  through Python and both Windows PowerShell hosts. The fixture explicitly
  decodes its UTF-8 stamp output and relocates its actual payload with the
  Unicode home. It protects every installation-state file; only Desktop
  PowerShell's exact CLR startup-profile cache is excluded as host-owned,
  not mistaken for an SDK write. Missing native assets, native-only fallback,
  malformed output, nonzero exits, unavailable commands and timeouts have
  explicit controls.
- The complete four-module consumer cohort passes 300 cases on Windows
  (one platform skip) and 284 on Linux (17 Windows-specific skips).
  The aggregate Linux selection and then its publication-policy module alone
  exceeded the unchanged 300-second group bound. All policy cases subsequently
  passed in three logical cohorts: publication/discovery, custom-bootstrap
  preflight and payload-stage policy. No test was omitted and no item, child,
  process, memory or group limit was raised.
- The [paired audit](audit.md#remaining-read-only-plan-cost-and-selected-context-validation)
  compares the actual original/current consumer against the same immutable
  inputs: medians 2.471 versus 0.403 seconds, with one fresh validation in both
  and the parent-owned PowerShell resolver call removed. These two-sample
  fixture measurements do not establish a whole-update or installed-state
  speedup.
- Unit and simple end-to-end tiers exercise actual resolver processes and
  stamped context records. Full clean-room runtime construction is unchanged
  and was not repeated; live host/external-venue rollout remains unauthorized.
  No dependencies were installed. The parent effort, installed-state proof
  and complete-update cost comparison remain open.

### 2026-10-22 - Presentation merged; remaining planning cost isolated

- #7908 merged after a current-head approval, no open findings and all required
  checks passing. Its source worktree remains responsible for this active
  effort, rather than finalizing after a presentation milestone. Installed
  serving state and equivalent complete-update cost remain unproven.
- A paired experiment through the real read-only plan builder retained
  identical results, helper-action counts and fixture bytes/mtimes across
  missing, standalone, managed, invalid, unsupported and foreign publication
  records, plus a managed healthy runtime. It changed only the previously
  reviewed Python site-initialization flag, not policy or helper contents.
  The healthy plan used one fresh probe and measured medians of 0.617 seconds
  with site initialization and 0.543 without. Other cases were noisy and
  sometimes reversed; this does not explain or resolve a minute-long live
  reconciliation.
- The explicit cross-plugin context route still starts PowerShell to invoke
  the same payload-owned resolver. In a real existing governed-plan fixture,
  that native call took 2.348 seconds versus 0.524 for the same payload's
  isolated portable resolver, with identical complete JSON, stderr and exit
  status and unchanged fixture bytes/mtimes. This is one isolated route, not a
  deployed-flow extrapolation. The next bounded slice must retain a fresh
  validation, native compatibility fallback, admission checks and refusals
  while avoiding the measured extra shell startup.

### 2026-10-22 - Remaining stamp and initialization decoration qualified

- Eleven PowerShell stamp producers and their four decorated Bash
  counterparts now report their existing action and deferral reason through
  the existing progress helper. Index still explicitly defers construction
  to setup, not first use. Containers, MCP and Machines report initialization
  through the same helpers on both platforms, without a decorative completion
  banner before cleanup. Bridge's PowerShell provisioning action is ordinary
  progress too.
- Only these emitters and adjacent routine blank writes changed. All eleven
  complete PowerShell entrypoints parse and retain identical nonpresentation
  tokens after checkout-line-ending normalization. All seven changed Bash
  entrypoints pass native syntax checks. Status/uninstall output, interactive
  policy prompts, arbitrary child lines, helpers, native arguments, cleanup
  and programmatic CLI/protocol streams are unchanged.
- The existing native emitter test now executes the affected helpers and
  literal calls on PS5, PowerShell 7 and Bash. Real staged stamp controls on
  both Windows hosts preserve deferral reasons and the final exit outcome,
  without banners or empty framed records. The complete Windows
  updater/materialization cohort passed 229 cases with two platform skips;
  the exact frozen candidate's Linux counterpart passed 191 with 40 platform
  skips. All thirteen install contracts and canonical declarations pass.
- Unit and simple end-to-end tiers cover source emitters, actual staged first
  stamps and release materialization. This changes presentation only, so
  unchanged full runtime-assembly clean-room scenarios were not repeated.
  No live host/external-venue update is authorized, and no no-op cost or
  deployment improvement is claimed. The broader presentation, installed-state
  and equivalent-operation-count completion gates remain open.

### 2026-10-22 - Machines qualification merged; presentation cleanup next

- #7901 merged after a fresh approval at the Python 3.10-compatible head,
  resolution of its collection finding and all required checks passing.
  The attribution check initially encountered an empty HTTP 500 response
  from GitHub's commit-to-PR endpoint; one diagnosed retry passed without
  changing metadata rules, adding a dummy commit or bypassing CI.
- All thirteen PowerShell producer declarations are now qualified at their
  actual entrypoints. This is source qualification, not installed-release
  proof or an equivalent healthy-no-op measurement. The effort and #6600
  remain open; no further live host update is authorized.
- The remaining presentation audit distinguishes installer-owned stamp,
  initialization and provisioning banners from status/uninstall output and
  contract comments. The next slice removes decoration at those emitters,
  retaining deferral reasons, diagnostics and the admitting parent's timed
  outcomes. It must not filter arbitrary child output or alter Bridge's
  programmatic CLI/protocol streams.

### 2026-10-22 - Machines nested producers qualified before publication

- Real native fixtures reproduced legacy-encoded construction stderr at
  success and failure on PS5 and PowerShell 7, plus the actual finite Python
  lifecycle refusal's legacy-encoded non-ASCII diagnostic. The constructor
  now uses its existing dual-stream helper and producer writer. The Python
  lifecycle producer initializes only explicitly requested redirected UTF-8
  text writers, retaining its original encoding-error policies.
- The actual legacy-probe/staged-stamp route exposed PowerShell 5.1
  module-preparation progress serialized as CLIXML on stderr. The shared
  decision child's own command now suppresses progress before module loading.
  Its ordinary diagnostics, including successful warnings, and refusal
  outcomes remain intact. Canonical controls exercise both actual projected
  callers, Machines and Index, at success and nonzero native status.
- Machines' actual `init.ps1` now carries the canonical guarded initializer
  and source-bound declaration. The native helper implementation, original
  child arguments, source checks, constructor callback, staging/watchdog,
  receipt and lifecycle authority are unchanged. Long-running delegates were
  not replaced by buffered capture wrappers.
- Required PR CI rejected the six-line growth of the grandfathered lifecycle
  module. The unchanged CLI parser/JSON-error adapter and requested-writer
  initializer now live in a private CLI module loaded by the existing absolute
  loader before SDK imports, with bytecode writes still disabled. The main
  lifecycle module is 931 lines and the adapter 113; no baseline was widened.
  AST comparison preserves the original CLI statement body and help description.
- First-touch fixtures use the original installer, real legacy gate and
  source snapshot under an empty profile, including quoted/non-ASCII paths.
  The format fixture copies its actual script/governance closure rather than
  claiming to qualify a complete runtime assembly. An initial cold copy of
  the full materialized payload exceeded its original 20-second fixture
  bound; the bound was not raised and no production copy-speed improvement
  is claimed. Fixture children now use the existing strict kill-on-close Job
  primitive so timeout cleanup cannot leave descendants holding pipes.
- The updater/materialization/probe Windows cohort passed 249 cases with
  seven platform skips. The full native/lifecycle runtime cohort passed all
  49 cases after the CLI extraction, including compact JSON on PS5 and
  PowerShell 7 at both output-request modes and the maintenance-token controls.
  The native Linux five-module cohort passed 238 cases with 56 platform
  skips. Materialization covers all thirteen producers; actual standalone
  entrypoint controls retain optional-helper absence and requested-missing-helper
  refusal.
- The broad qualification exposed stale tests, not a license to ignore
  failures: the Index status fixture now includes its real packaged scripts
  and materialized engine; boundary assertions follow the current
  publication guard, metadata-only startup and atomic publication owners.
  A POSIX-only systemd case is classified explicitly on Windows while its
  Windows task counterpart remains covered. A synthetic maintenance-profile
  test no longer accidentally consumes the runner's real WSL kernel identity.
  No guest registration or production policy was changed.
- Unit and simple end-to-end tiers include real native diagnostics,
  admitting-parent capture, unchanged unrequested bytes, compact lifecycle
  JSON and first-stamp refusal/absence controls. Full clean-room runtime
  construction is unchanged and was not repeated; no host or external-venue
  update is authorized for this candidate. No runtime dependency was installed.
- Review caught a Python 3.10 collection regression in the new native fixture's
  version lookup. It now reads the synchronized `plugin.json` version using
  the already-imported JSON parser. All ten nodes collect on the existing
  Python 3.10.20 interpreter and the native controls pass again. Only missing
  conditional Pytest dependencies were restored into isolated task scratch;
  no interpreter, plugin runtime or existing test environment was modified.
- Documentation impact is limited to the plugin's output boundary, the
  shared install contract and the canonical legacy-probe reference. This is
  a qualified source candidate, not a merged or installed release. Both
  parent objectives, remaining presentation work and equivalent no-op
  measurements remain open.

### 2026-10-22 - Budget staged output and native diagnostics qualified

- #7888 merged after current-head approval, empty review threads and all
  required checks passed. The authorized exception covered only the
  additional CODEOWNER vote, not CI. Both parent objectives remain open;
  this source merge does not establish promotion or installed rollout.
- Real PS5 and PowerShell 7 fixtures reproduced legacy-encoded construction
  stderr and first-stamp output before enrollment. The construction probe
  retained isolated Python flags, literal argv, successful warnings and
  failure decisions; the actual marketplace-path stamp retained spaced,
  quoted and non-ASCII runtime paths.
- Only the construction-shell diagnostic boundary changed: its existing
  dual-stream native helper now captures stderr explicitly and forwards it
  through the producer text writer, including on native success. The helper
  implementation, source checks, construction callback, staging/watchdog
  authority and transaction policy are unchanged.
- After native qualification, the actual `install.ps1` received the guarded
  canonical initializer and source-bound output declaration. Release
  materialization now covers twelve output producers without adopting
  another shared installer engine.
- The staged fixtures execute the original installer and its real self-stage
  route, not an injected encoded-command wrapper. Its `Start-Process`
  compatibility function can rewrite vector `-File` invocations, but the
  current self-stage call supplies a quoted native command-line string.
  Both hosts retained UTF-8 stdout without CLIXML, the original arguments,
  the source snapshot and no runtime construction.
- The full changed-module run passed 228 Windows cases with two skips and
  188 same-source Linux cases with 39 platform skips. Six final native
  fixtures also passed after tightening fixture asset requirements. Existing
  Budget staging/stamp selectors passed on both platforms; no dependency
  was installed. Optional-helper absence and requested-missing-helper
  refusal remain covered through the original entrypoint.
- Unit and simple end-to-end tiers include the real updater-to-staged-child
  capture with an empty home, declared snapshot, time-framed lines and
  retained completion status. This is first-touch stamping, not full clean-room
  runtime construction; that unchanged contract was not repeated. No external
  venue or host update is authorized for this candidate.
- Documentation impact is confined to Budget's installer section and the
  shared captured-output contract. Runtime JSON, output-free hooks, public
  CLI arguments and configuration remain unchanged.
- This qualifies merged source, not a promoted or installed release.
  Machines' remaining output boundary, equivalent no-op counts and
  costs, serving-state proof and parent launch/trust obligations remain open.
- Next: qualify Machines' actual `init.ps1` output owner. Its construction
  helper has the same raw stderr boundary, while legacy/cell routing adds
  nested PowerShell and Python producers that require explicit stream and
  protocol checks before enrollment.

### 2026-10-22 - MCP native diagnostics qualified before output enrollment

- #7879 merged after current-head approval, empty review threads and green
  required checks. The authorized extra CODEOWNER-vote exception did not
  bypass CI. Both parent trackers remain open; promotion and installed
  rollout are not inferred from this source merge.
- Real `-File` fixtures reproduced legacy-encoded stderr bypassing the text
  writer at both the construction-shell and GC count subprocess boundaries.
  Both PS5 and PowerShell 7 controls failed before either boundary was
  routed through the existing native helper.
- The two visible diagnostic sites now use that same dual-stream helper
  with explicit error capture and text-writer forwarding, including
  successful native commands' warnings. Construction flags, failure checks,
  callback ownership and GC protections are unchanged. The preceding GC
  command retains its explicit stderr suppression. Scalar count output is
  trimmed to preserve its original single-line presentation.
- Only after those native controls passed was `init.ps1` enrolled with the
  canonical initializer and source-bound declaration. Real updater first
  stamps, legacy/helper-absence controls and all eleven output producers'
  release-materialization fixtures passed. The MCP bridge, CLI JSON and
  stdio protocol are not decorated or re-routed.
- The final same-source qualification passed 237 Windows cases with two
  skips and 187 native Linux cases with 42 platform skips. Existing native
  cleanup, literal argv, large-stderr, construction-failure and timeout
  diagnostics remain covered under their original deadlines and containment
  limits. No dependency was installed.
- The broad run also exposed an outdated test that still rejected the
  public update request owner's now-supported `--json` option. Its coverage
  now verifies actual request dispatch without invoking that owner, while
  retaining rejection of undeclared `--quiet`; no updater policy changed.
- The simple end-to-end and first-touch tier uses an empty home and the real
  first-stamp adapter. Full fresh-machine construction was not repeated
  because the construction contract did not change. No external-venue
  update is authorized for this candidate; promotion and rollout remain
  separately owned obligations.
- This qualifies one more source boundary, not remaining Machines/Budget
  producers, equivalent no-op measurements or parent launch completion.
- The next output boundary is Budget's actual standalone installer, including
  its staged child invocation. Its compatibility launcher can rewrite a
  `-File` vector as an encoded PowerShell command, so ordinary non-staged
  first-stamp coverage alone does not establish the actual launch route or
  redirected stream shape.
  Qualify that native route before enrolling it; Machines remains unqualified.

### 2026-10-22 - Containers' declared producer retains native diagnostics

- #7854 merged after current-head approval, empty review threads and green
  required checks. The authorized review-rule exception covered only the
  additional CODEOWNER vote; no CI gate was bypassed. Both parent trackers
  remain open. This is merged source, not an installed release claim.
- A real native preflight fixture reproduced two defects before enrollment:
  inherited Windows stderr bypassed the requested UTF-8 text writer, and a
  nonzero Docker version probe still printed success. The POSIX counterpart
  suppressed stderr and likewise printed success on failure.
- PowerShell now reuses the installer's existing asynchronous dual-stream
  helper, retains native decoding and arguments, and forwards diagnostics
  through its stderr text writer. Both platforms retain captured stdout
  details and report the native exit code as a warning; Docker remains
  optional and installation continues.
- The output-only standalone registry identifies Containers' `init.ps1`,
  rather than assuming every producer is `install.ps1`. Existing
  synchronization and release materialization package the unchanged shared
  initializer and bind the declaration to the actual entrypoint. This does
  not adopt the construction engine or change policy, lock or runtime owners.
- Windows qualified 268 cases with nine platform skips; the same frozen
  candidate qualified 225 cases with 52 platform skips on native Linux.
  Coverage includes PS5 and PowerShell 7 legacy/requested encoding controls,
  real child exit codes and diagnostics larger than a pipe buffer, unchanged
  native decoding/stdin, the updater's actual first-stamp adapter, legacy
  helper absence, fail-closed requested initialization, and canonical plus
  materialized release declarations. The original per-test and group
  containment limits were unchanged.
- The first broad Windows run exposed two missing-source import failures in
  the reused test environment. Both controls passed after explicitly exposing
  the plugin's declared local source-library closure; the complete final
  run above used that closure. No dependency was installed or policy relaxed.
- The first PR guard required a Worktrees changefile even for its tests-only
  diff. Added the required development entry rather than bypassing or
  weakening that gate; the Worktrees runtime payload itself is unchanged.
- Validation tiers: focused logic and native subprocess coverage passed;
  the real updater stamped an empty fixture home without constructing a
  runtime; same-root and separate-tree release fixtures passed. A full
  fresh-machine provision was not repeated because construction did not
  change. No further external-venue update is authorized for this slice, so
  installed promotion and rollout remain unproved, under the existing owner.
- This is one source boundary, not completion of the remaining standalone,
  equivalent no-op cost, rollout or parent launch obligations.
- Next source slice: qualify MCP's actual `init.ps1` producer. Its native
  work already passes through the existing dual-stream helper; retain that
  behavior and prove the actual first-stamp and encoding boundaries before
  declaring capture support. Machines and Budget remain separate unqualified
  boundaries.

### 2026-10-22 - Second authorized capture changed releases again

- The separately authorized ordinary current-state capture completed with
  exit zero in 2,056.7 seconds. Another release arrived before acquisition:
  Manager and all thirteen runtime payload generations changed. This is
  another changed-version update, not an equivalent unchanged-flow benchmark.
  No further update or repair is implied.
- Runtime execution accounted for 1,646.1 seconds: Worktrees 203.2, Bridge
  221.1 and remaining runtime reconciliation 1,221.8. The registered payload
  phase was 209.1 seconds and runtime preflight 23.6 seconds. These are
  measured phase durations, not exclusive attribution to individual
  package, network or probe commands.
- The running engine now correctly framed registered payload acquisition
  under `payload_refresh`, independently confirming the previously installed
  #7801 behavior. Read-only post-command checks found matching selected and
  deploy identities for the investigated runtimes; the new Bridge/Index
  services remained healthy after the captured management channel closed,
  and both recorded predecessor processes were absent.
- Installed payload and selected runtime bytes for the #7828 acquisition
  fix now exactly match the merged source, as do the earlier #7801 and
  #7818 modules. No sharing failure occurred during this run, so this is
  installed-source and ordinary-operation evidence, not live execution of
  the failure-classification branch.
- The Worktrees monitor seam again reported the explicit `no-live-monitor`
  outcome. The retained unpublished monitor observation is not repaired or
  reclassified by these service checks. Required-role applicability and
  existing serving-convergence ownership remain separate.
- Credit the independent shared-lock recovery implementation in #7814,
  actually merged, rather than the earlier still-open proposal observation.
  Its native/standalone source work does not itself prove live repair or
  original-operation completion; #7117 and its existing owner retain those
  acceptance obligations.

### 2026-10-22 - Payload reporting fix merged; changed-version update proof bounded

- #7828 merged as `2d42fad24d9724d677bc3eaea3497bf6b4ab6497` after
  Copilot approved exact head `84d3c6994bc5bf98bb5daef1c77885d8ae30755e`,
  its actual review reported no unresolved findings, discussion threads were
  empty and every current-head required check passed.
- The publication rebase retained the qualified acquisition, event adapter,
  three covering test files and results reference byte-for-byte. Backed up
  the approved tip and advanced only provider-confirmed merged history.
  Re-read the Plan and Validation Plan; only the narrow diagnostic item is
  closed, not serving convergence or the complete observability campaign.
- A separately authorized ordinary x64 update completed with exit zero in
  2,144.9 seconds. It changed runtime generations and did not reproduce the
  historical sharing failure; it is not an equivalent no-op benchmark.
  Read-only checks established selected/deploy agreement for the three
  investigated runtimes and healthy newly selected Bridge/Index services.
- The installed payload and selected Worktrees runtime contain the exact
  merged #7801 phase-restoration and #7818 no-site-probe source bytes.
  That does not mean the old updater process used those bytes earlier in
  the same command, nor that #7828 is deployed.
- Do not infer monitor readiness from update success: its installer reported
  `no-live-monitor`, while read-only inspection found a retained older,
  unpublished process responding with an unavailable projection publisher
  and stale owner/route metadata. Process existence alone does not establish
  a live owner, a required active role or permission to restart it. Preserve
  this as a separate serving-state observation under #6389 and the existing
  #5453/#5512 owners, not a cause of the unreproduced sharing failure.
- No additional update, service repair, process termination or other-host
  rollout was performed. The next equivalent cost measurement and any
  live recovery retain their explicit authorization gates.

### 2026-10-22 - Retain native payload failure classifications before truncation

- A failed-first regression reproduced loss of an early sharing/lock
  violation from a long child diagnostic: retry admission used complete
  output, but the advisory/final status retained only its tail. The latter
  could lose the specific event code and change a final contention exit
  from 5 to 1. Native I/O refusal wording also lost its recognized class
  once the numeric marker was outside that tail.
- Preserve an evidenced Windows sharing/lock marker in the status reason
  before the existing redacted, bounded tail. Use the existing recognized
  I/O-error wording. Host-aware numeric admission, refusal precedence,
  retry count/backoff/budget, activation preservation and timeout settlement
  are unchanged. This is error reporting, not identification or repair of
  a file-handle owner.
- The focused regression first failed twelve cases and passed four controls.
  The complete affected Windows acquisition/telemetry suites then passed
  213 cases with two platform skips, including real failed child commands,
  exact attempt counts, final exit classification and the runtime-mutation
  barrier. The same frozen candidate passed 184 cases with 31 platform skips
  on native Linux. Install-contract, lint and documentation checks passed.
  No test limit or runtime dependency changed.
- Validation tiers: unit and real-subprocess failure paths passed on both
  native platforms. Fresh runtime construction is not changed, so a complete
  clean-room installation was not repeated for this reporting-only slice.
  Live deployment of this source is not authorized; the ongoing diagnostic
  update cannot install an unpromoted change and is not its rollout proof.
- The live diagnostic update remains separately owned and cannot be treated
  as validation of this unpromoted source change. Equivalent whole-flow cost,
  remaining standalone boundaries, authorized rollout and parent #6600 stay
  open.

### 2026-10-22 - Probe invocation optimization merged; continue measured diagnosis

- #7818 merged as `3517f2af3db876e546fb1f24854bd8f08b0c57a9` after
  Copilot approved exact head `c1d28cd9cb0f3dd56bd82be9aa9d4573a76f3f76`,
  its actual review reported no open findings, discussion threads were empty
  and every current-head required check passed.
- The publication rebase preserved the qualified probe code, tests and
  directly related canonical helper/procutil closure byte-for-byte. Retained
  the approved tip on a backup and advanced only confirmed merged history.
  Re-read this effort's Plan and Validation Plan and closed only the specific
  unused-site work item; whole-flow cost and rollout remain open.
- The one authorized ARM64 recovery/update is complete and its selected
  services were independently checked. No additional update on another host
  is implied. Other observed sharing errors still lack handle-owner evidence;
  old native installer failures cannot be diagnosed from final exit codes alone.
- Next diagnosis must retain complete attempt output and distinguish final
  required failures from recovered advisory attempts. The source currently
  bounds failed payload diagnostics to a short tail; a source-supported
  classification-loss hypothesis is not proof of the other host's lock holder.
  Keep #7117's independent source recovery owner and the existing rollout holds.

### 2026-10-22 - Isolate repeated probe startup before changing its invocation

- The installed planning observation attributed 9.201 seconds to eleven
  serial helper invocations, not to their authority checks alone. An isolated
  eight-process experiment separated interpreter startup, helper loading and
  the actual probe body using the canonical helper closure and synthetic
  legacy/policy fixtures. No deployed files or real installation state changed.
- With the same Python 3.11 interpreter, sampled empty starts took 160/134 ms
  with/without site initialization; direct allowed probes took 474/345 ms and
  invalid-policy probes 409/383 ms. Instrumented warm calls attributed 246/205
  ms to loading and 38/36 ms to the probe body. These small samples are not an
  equivalent installed-flow speedup or proof that all remaining time is idle.
- Complete allowed and invalid-policy JSON, exact exit status and every
  fixture byte/mtime were unchanged. Disabling site initialization does change
  the pre-3.14 venv prefix and removes site/editable import paths: those changes
  are explicit, not filtered from the evidence. The standard-library-only
  helper does not use those prefixes or paths for its authority decisions.
- The source now adds `-S` only to this isolated, payload-owned Python probe.
  It preserves `-I`, UTF-8, the selected executable, per-attempt validation and
  the native fallback. The existing six Windows authority scenarios compare
  complete output and exit status against site-initialized Python in addition
  to their native-adapter comparison. The exact-argv regression failed first.
  Complete covering reconciliation/probe suites passed on Windows (173 passed,
  one platform skip, 135.50 seconds) and the same frozen source on Linux (168
  passed, six platform skips, 40.12 seconds), at the unchanged limits.
- The first Windows attempt could not collect because the cached environment
  lacked a newly declared local dependency. The canonical prepare-only path
  restored that environment; the actual regression then failed as expected
  before the source change. Install-contract, lint, documentation and effort
  structure guards pass. This is a below-altitude invocation optimization,
  not a new planner, cache, installation policy or service.
- Documentation impact: Worktrees' README owns the selected-interpreter,
  standard-library and pre-3.14 prefix explanation; the source-owned effort
  owns the limited cost evidence. A patch changefile names Worktrees only.
  Clean-room installation was not run because no construction or first-install
  code changed; fresh isolated probe consumers were exercised instead. Live
  execution of this unpromoted candidate is not authorized by an earlier
  update's consent. Review, release and installed qualification remain open.
- A fresh read of #7117 found a separate shared-lock-recovery claimant.
  Preserve that source ownership rather than opening a competing repair.

### 2026-10-22 - Explicit recovery completed one normal update, not fleet rollout

- After explicit operator authorization, an ordinary one-file publication-lock
  directory was archived atomically to a unique sibling, preserving its owner
  receipt. The recorded owner was absent, a full host restart occurred after
  acquisition, the exact receipt remained unchanged and no installer was active.
  No automatic stale-lock reclaim, force update or service termination was used.
- The single authorized normal ARM64 update then exited zero: Manager took
  2,568.6 seconds, engine execution 2,450.0 seconds and runtime reconciliation
  1,302.8 seconds. Worktrees took 416.6 seconds, Bridge 372.1, Codespaces 230.7,
  Dispatch 286.3 and Index 144.8. These are changed-version flows, not healthy
  no-op measurements or evidence that every host shares the original failure.
- Read-only post-update checks confirmed matching selected/deployed versions
  for Worktrees, Bridge and Index. The monitor reported ready with current
  roster readiness; Bridge reported healthy, ready and not draining. Index
  retained its client role without activating a service. The publication lock
  was absent and the archived receipt's digest was unchanged.
- Bridge recovered a prior aborted cutover, then completed its normal healthy
  transition with a clean unforced drain, predecessor exit and relay adoption.
  That successful recovery does not identify why the earlier cutover aborted.
  Anchor synchronization explicitly skipped a stale-index condition; no anchor
  repair was attempted. Other historical host failures remain distinct.
- Payload and runtime module hashes matched the source before #7801, not its
  phase correction. Therefore this run proves neither that correction nor the
  new probe optimization live. No second update was started. The supported
  explicit recovery command remains with #7117's existing claimant; the
  broader effort, safe rollout holds and parent launch obligations stay open.

### 2026-10-22 - Attribution correction merged; measured planning costs remain

- #7801 merged as `74753fa551edcd4cc91ee395fa4698331e2452de` after
  Copilot approved exact head `91b3e5c78999cdf8b039ddb16d68d6e54d4f6b7a`,
  the actual review reported no open issues, discussion threads were empty and
  every current-head required check passed. The initial changefile failure was
  fixed by moving client-specific documentation to its actual owner, not by
  bypassing CI or creating an unrelated Manager release.
- Re-read the effort's Plan and Validation Plan after actual merge. Closed
  only the source phase-attribution item, retained the published tip on a backup
  and advanced confirmed merged history. This does not prove installed rollout,
  publication recovery, shorter planning or completion of the effort.
- A separate installed Windows `reconcile-plugins --peek` observation exited
  successfully in 19.093 seconds with unchanged plan-cache bytes and mtime.
  Eleven serial installation-context probes occupied 9.201 seconds; 42 Git
  commands occupied 3.815 seconds. Their non-overlapping union was 13.016
  seconds. The remaining 6.077 seconds includes startup, Python work, gaps and
  capture completion; it is not demonstrated idle time.
- The observer's 162 process records matched its Job accounting total, with a
  peak of seven concurrent processes. Launcher/worker lifetimes were not
  double-counted in the logical spans. Observer work overlapped the invocation;
  these figures are neither a speedup against the earlier 17.610-second run nor
  qualification of the current Dev source. The selected catalog declared an
  older installed payload, so further attribution must retain that provenance.
- Next cost work must isolate repeated helper loading from authority checks
  and investigate repeated Git/context preparation under #6086's existing
  ownership. Keep per-attempt authority, fresh planning and execution admission.
  The separately authorized live recovery remains conditional on a safe idle
  window; another session's maintenance must not be interrupted.

### 2026-10-21 - Reproduce update failures and correct payload cost attribution

- A newly captured normal Windows ARM64 update refreshed its payloads, then
  failed before Worktrees runtime construction: the command-binding publication
  lock named a dead owner and required explicit repair. The stale lock and
  missing supported recovery entrypoint are already tracked in #7117. No lock
  was removed, archived or silently reclaimed. Other observed payload sharing
  errors, native installer failures and historical deadline failures remain
  distinct cases rather than one inferred fleet-wide cause.
- The same stdio exposed a concrete attribution defect: a 1.727-second
  marketplace auth report retained its phase while registered payload refresh
  continued for another 224 seconds. That work appeared under a 226.6-second
  `auth_report`, although it was not authentication. Runtime preflight in this
  run was 18.7 seconds; a prior run's 56.759 seconds is not evidence of one
  particular slow probe.
- Registered plugin refresh now restores the existing `payload_refresh`
  facade after both ordinary and fallback marketplace authorization. This
  preserves the auth session, required-context promotion, credential overlays,
  acquisition order, exit status and structured outcomes. It corrects both JSON
  phase accounting and human framing, without a new clock or reporting owner.
- Deterministic regressions exercise real session preparation with stubbed
  external operations. Four fresh/fallback cases first failed on the wrong
  phase; two already-prepared cases already passed. The final cases check
  refresh, browse, acquisition and summary attribution, phase closure, human
  framing, clean stdout and unchanged success/failure/auth behavior.
- Full covering qualification also found an incomplete native smoke fixture.
  It now copies the installer's two unconditional companion scripts while
  still omitting the optional human-output initializer. Both Windows shells
  prove ordinary smoke execution needs no initializer and requested capture
  explicitly refuses its absence; the shipped installer is unchanged.
- Final authentication, auth-usage and telemetry-wiring suites passed on
  Windows (265 passed, two platform skips, 126.40 seconds) and the same frozen
  source on Linux (245 passed, 22 platform skips, 68.57 seconds). The original
  30-second item, 300-second group and resource caps remain. Linux needed the
  canonical packaged test environment: isolated credential children correctly
  ignore source-only `PYTHONPATH` injection. The hardened wrapper was attempted
  but its host CLI could not run without native Node, so qualification used
  the documented bounded-runner fallback with prepared test dependencies.
- Fresh-machine construction is not exercised by this phase-boundary change.
  The live update above establishes the failing attribution contract, not
  deployment of this unpromoted candidate; installed validation still belongs
  to the explicitly authorized rollout owner.
- CI correctly detected that putting this client-specific note in a shared
  library README would change the Manager's packaged inputs too. Moved the
  note into the owning Worktrees README instead; the shared library remains
  unchanged and this correction does not cause an unrelated Manager release.
- This source correction does not fix publication recovery or claim a faster
  reconcile plan. Safe explicit recovery, component-level cost attribution and
  equivalent before/after measurements remain open, as do promoted-installed
  qualification and the effort's original ownership holds.

### 2026-10-21 - Remaining identity banners merged; keep protocols undecorated

- #7754 merged as `53ea6f887d843698f9db55bce62423e0f0f50310` after
  Copilot approved exact head `9ed44f690b4eaf24435d8adc406c003c6b59e420`,
  the actual review reported no open findings, discussion threads were empty
  and every current-head required check passed.
- Re-read the effort's Plan and Validation Plan after actual merge. Retained
  the published source tip on a backup and advanced confirmed merged history.
  This closes the inventoried literal install/update identity-banner blocks,
  not remaining standalone timing, whole-flow measurement or rollout gates.
- The operator reiterated the programmatic-CLI boundary: protocol and
  machine-readable exchanges must remain free of human presentation. These
  edits target native installer statements only, not Bridge command handlers
  or message protocols. No general command-stream formatter is introduced.
- The effort stays active with its original ownership/deployment holds; this
  worktree is not finalized. Continue remaining standalone-output work only at
  an admitted installer output owner, never by decorating a general CLI.

### 2026-10-21 - Consolidate the remaining literal installer identity banners

- The bounded emitter inventory found remaining install/update action banners
  in Dispatch, Vault, Pull Requests, SSH and Budget Guidance. Their existing
  start/completion locations now use their ordinary progress/success helpers;
  Dispatch's superseded-install branch retains its full qualification through
  the existing skip helper. Only these literal output blocks changed.
  Uninstall banners, tips, arbitrary diagnostics and control flow remain.
- Extended the existing installer-engine qualification module with one batched
  native fixture per available shell, executing all fifteen shipped identity
  statements and their actual helper definitions. Standalone output has one
  record per statement and no adjacent decorative blank emitters. A second real
  child traverses the canonical human adapter and verifies running/done/skipped
  elapsed records and the actual child-exit duration. This is not a new
  required-CI native matrix or another installation owner.
- The initial contract failed under both Windows shells before emitter edits.
  Full stable-snapshot installer-engine tests passed on Windows (51 passed,
  one platform skip, 30.41 seconds) and POSIX (50 passed, two platform skips,
  2.87 seconds), covering source-bound declarations, materialized payloads,
  capability refusals and the native action statements. Original 30-second
  item, 300-second group and resource limits are unchanged.
- Regenerated the four eligible source-bound human-output declarations; Budget
  Guidance has no such declaration, so no new output admission was invented.
  All thirteen installer contracts, Python lint and documentation consistency
  pass. The shared install contract documents the emitter behavior, and one
  patch changefile names all five changed plugins.
- Publication found a real upstream Dispatch declaration conflict. Retained
  the qualified commit on a backup, preserved the non-overlapping callback
  co-deployment change and regenerated only the conflicted declaration through
  its canonical owner. Full rebased-source qualification passed again:
  Windows 51 passed/one platform skip in 32.32 seconds and POSIX 50 passed/two
  platform skips in 3.18 seconds. No installer policy or conflict side was
  discarded to make publication succeed.
- Fresh-machine provisioning and live external deployment were not exercised:
  no construction, activation, service or prerequisite behavior changed, and
  isolated native execution covers the changed statements without touching a
  deployment. Standalone timing boundaries, whole-flow measurements and the
  existing promoted-installed/ownership holds remain separate open gates.

### 2026-10-21 - Bridge action identity merged; retain the whole-flow gates

- #7745 merged as `416862976cafc23f7b95af5397c954ae5f61e079` after
  Copilot approved exact head `fdbb12c8d50191fd67ce956544d0fd2c4e10a524`,
  the actual review had no open findings, discussions were empty and all
  current-head required checks passed.
- Re-read the effort's Plan and Validation Plan after actual merge. The four
  Bridge action-header blocks are closed, not the broader banner or standalone
  output items. Retained the published tip on a backup and advanced only
  confirmed merged history. The active effort's worktree is not finalized.
- Continue consolidating remaining installer-owned identity banners without
  generic child-output filtering, while preserving the original structured
  context owner's separate seam. Whole-flow cost and installed-release
  qualification remain open under the existing deployment/session holds.

### 2026-10-21 - Replace Bridge's decorative action banners at the emitter

- Traced the launch parent's ordinary line relay and Bridge's actual native
  install/update entrypoints. Replaced only their four blank/banner/blank
  blocks with the existing progress helper. The standalone action identity
  remains, while a timed parent renders one running line rather than three
  decorative records. No general banner regex or blank-line filter was added.
- The new native regression first failed for both actions under PowerShell 7
  and Windows PowerShell 5.1. Final Windows identity/lifecycle coverage passed
  18 cases with 15 platform skips in 32.98 seconds; the staged-source POSIX
  counterpart passed 16 with 17 platform skips in 7.85 seconds.
- Both standalone and real captured-child cases execute the shipped action
  and progress helper, with a failing prerequisite before deployment. They
  preserve an arbitrary diagnostic banner and empty line, verify exactly one
  action record, canonical UTC/running/elapsed framing and the real exit-one
  completion. The existing lifecycle tests retain their outcome/cleanup checks.
  Original item, group and resource limits remain unchanged.
- The generated source-bound native-output declaration and install-contract,
  payload-generation, Python lint and documentation guards pass. Bridge's
  README records the presentation contract. This is below the existing
  reviewed architecture; no installation, lease, cutover or release policy
  changed. Two decorative records per action are removed; no execution-time
  improvement or equivalent whole-flow cost result is claimed.
- Fresh-machine provisioning and live external deployment were not exercised:
  the changed statements precede prerequisite admission, the isolated native
  fixture covers their actual output contract, and rollout remains held under
  the effort's existing owner. Other installers' banners, standalone output,
  full-flow measurement and promoted-installed proof remain open.

### 2026-10-21 - Native vocabulary merged; continue at the remaining owners

- #7736 merged as `a2c6cf215e7bfdb133020cfaeebc08ba662883cc` after
  Copilot approved exact head `8258d3b1926441805bc07807344bd90b162bd7a5`,
  the actual review reported no blocking findings, there were no inline
  threads and all current-head required checks passed.
- Re-read the effort's Plan and Validation Plan after actual merge. Closed
  only native marker normalization, retained the published tip on a backup
  and advanced confirmed merged history. No whole-flow speed or deployment
  claim follows from the presentation fixtures.
- Next presentation work must distinguish a redundant installer banner from
  useful child diagnostics at the actual native output owner. The separate
  structured-context pump still awaits the original #6928 owner; no competing
  wait/termination adapter or second ownership request was introduced.
- Standalone coverage, full-flow formatting and equivalent cost/installed
  evidence remain open. Existing deployment and protected-session holds are
  unchanged; the effort is active and this source worktree is not finalized.

### 2026-10-21 - Normalize native progress markers at their existing sinks

- The reviewed presentation rule already names one state vocabulary. Existing
  captured native helper lines still carried `[OK]`, `[SKIP]`, `[WARN]`,
  `[FAIL]` and `...`. Python's human renderer and the native PowerShell
  setup formatter now translate only leading, unqualified progress markers
  to `done`, `skipped`, `warning`, `failed` and `running`.
- This is presentation, not new result inference: a child diagnostic remains
  an elapsed progress line. It does not complete its operation or override
  actual exit codes/structured outcomes. Explicit owner states, completion
  messages, unknown/embedded markers and canonical child envelopes are
  unchanged. Recognition follows existing redaction and physical-line
  splitting; state is local to each line.
- Three failing-first cases reproduced the mixed vocabulary in the shared
  Python renderer and both native Windows hosts. The real maintenance
  producer now emits a helper marker and verifies one normalized frame while
  retaining its argument, environment, exit and fresh-replanning checks.
- Complete contained shared-telemetry, native launch-preparation and fresh
  bootstrap-output suites passed 225 cases on Windows (45 platform skips,
  185.91 seconds) and 189 on Linux (81 platform skips, 42.67 seconds). Item,
  group and resource limits remain unchanged. Fresh isolated bootstrap
  helpers and real native consumers are exercised; full runtime construction
  and installed rollout are not claimed by this presentation-only slice.
- Regenerated the three shipped bootstrap-helper copies from canonical
  source: Logger's script package and Worktrees' script/runtime packages.
  No generated command invocation, admission, lifecycle, wait or updater
  owner changes. Other standalone/context output, banner convergence and
  equivalent whole-flow/installed qualification remain open.
- Documentation impact: the shared human-output reference and Manager's
  launch-preparation reference now state the exact marker mapping and its
  outcome-preservation limit. This is a below-altitude correction under the
  existing reviewed plan, not a new architecture or daemon. Required source
  review, CI and merge remain pending.

### 2026-10-21 - Fallback correlation merged; retain the broader timing gate

- #7727 merged as `5fc3ba92858c545cf61a20c9f83a427d7b6c4627` after
  Copilot approved exact head `ec9b55c8964a9887a0d18ce188b85409ec99c92d`,
  the actual review reported no findings, no inline threads remained and
  current-head required CI passed. This is source completion only.
- Re-read the Plan and Validation Plan after actual merge. Recorded the
  fallback-correlation checkbox only; explicitly unavailable elapsed time is
  not evidence of measured install/update cost. Retained the published tip
  on a backup and advanced only confirmed merged history.
- The next standalone trace follows Index's existing native self-stage parent.
  Its PowerShell encoding initializer is not a formatter or wait owner, and
  its native watchdog is not the Worktrees command lifecycle. Reuse the actual
  owner rather than adding a competing lease/watchdog for presentation.
- Other standalone/context output, banners, equivalent whole-flow costs and
  promoted installed qualification remain open. No deployment, release-policy,
  activation, existing hold or ownership state changed.

### 2026-10-21 - Correlate Bash fallback diagnostics without invented timing

- Classified the remaining fallback callsites: argument/recovery/runtime
  setup before Python is available, background-stage dispatch and resolve
  before the Picker, and late launch/mux/post-exit diagnostics. These do not
  share one finite monotonic scope; keep the existing maintenance and preflight
  clocks local rather than extending a renderer across an interactive wait.
- Reuse the existing unavailable-timing sink for inactive `setup_log` and
  `setup_status`. It now reads UTC wall time once per message, frames every
  physical line and explicitly reports elapsed unavailable. A failed wall
  clock is labeled time unavailable. Quiet stdout and the status echo gate
  remain unchanged; no Python, new renderer or timing authority is introduced.
- Four native regressions failed first against the old local-time grammar.
  The complete POSIX fallback/preflight, passthrough and maintenance selection
  then passed 70 cases with 89 platform-specific skips in 47.27 seconds under
  the original item, group and resource limits. Existing finite-clock and
  renderer-failure contracts remain in that selection. The synthetic fallback
  uses a non-UTC timezone and proves three clock reads for three multiline
  messages, with no active renderer/job. This is not a new speedup claim.
- The existing native Windows UTC/Stopwatch and gated status contracts passed
  all eight selected cases on both PowerShell hosts in 9.10 seconds. Python
  lint, effort structure and the 13-plugin install-contract check passed.
- Windows already uses native UTC plus its real Stopwatch; its launcher is
  unchanged. The changed Bash helpers run from fresh isolated log fixtures.
  Full runtime construction and external installation are not exercised by
  this output-only correction; promoted installed qualification remains gated
  by the existing release/deployment holds. The larger timing, presentation
  and whole-flow measurement goals remain open.
- Documentation impact: Manager's launch-preparation reference now explains
  the fallback boundary and its intentionally unavailable duration. This is
  a below-altitude presentation correction within the reviewed observability
  plan, not a daemon or lifecycle change. Required review/CI and merge follow.

### 2026-10-21 - Count-only source boundary merged; continue the output audit

- #7708 merged as `9b14d71afdce2b3d85c5917f6e11cfdfea835885` after
  Copilot approved corrected head `4e36b0386c97ce3a78f1e7cc94a06c896d0d1da2`,
  the direct-handoff finding was resolved and current-head required CI passed.
  The missed direct path was reproduced and corrected, not treated as harmless
  because the initial source slice already had green checks.
- Re-read the Plan and Validation Plan after the actual merge. Closed only
  routine command-echo suppression, retained the published tip on a backup and
  advanced only confirmed merged history. The source effort remains active.
- Continue tracing the early/late Bash fallback logger and its bounded
  formatter callers before choosing another presentation change. The fallback
  still uses local wall time without a monotonic suffix; do not replace it
  with wall-derived elapsed time, a process per line or a formatter that lives
  across the Picker or interactive session.
- #6928 remains open on its original contributor head. The public coordination
  thread now also has a maintainer request to resume that original change and
  account for the output-pump seam. Preserve that owner's wait/watchdog scope;
  no competing context adapter or second coordination request was created.
- Other output boundaries, banner convergence, equivalent whole-flow costs and
  promoted installed qualification remain open. No update, runtime repair,
  activation, deployment hold or ownership state changed.

### 2026-10-21 - Keep argument values out of routine launch diagnostics

- Both launchers now log passthrough argument counts instead of values;
  Windows pane preparation likewise records only the count. Removed duplicate
  bootstrap and reconciliation command echoes, retaining their service labels,
  timed outcomes, admission diagnostics and child output. This does not add a
  generic redactor or promise to sanitize arbitrary child-emitted output.
- Native source-prefix and pane-preparation fixtures first reproduced raw
  private arguments in the log, then proved exact forwarding with count-only
  diagnostics. The existing native maintenance fixture now includes a private
  command/argument sentinel, including allowed, refused and current plans,
  while retaining child execution, environment, exit and replanning assertions.
- The Windows selection passed 69 cases and the POSIX selection passed 46,
  with platform-specific skips. Each also found the same overly broad pane
  guard: it banned encoded commands across the whole launcher, including the
  already-reviewed detached observer. Scoped that ban to the actual pane
  creation path; the pane, install-contract and headless guard selection then
  passed 306 cases with 15 platform-specific skips. These counts overlap;
  no timeout, native boundary or case was removed.
- The fixtures exercise fresh isolated log directories and real native shell
  consumers. Full runtime construction and live external installation are not
  claimed: this slice changes diagnostic content only, and promoted installed
  qualification remains subject to the existing rollout gate. No runtime,
  installer, staging parent, watchdog or publication authority was changed.
- Updated Manager's launch-preparation documentation and its patch changefile.
  This is a below-altitude presentation correction within Phase 2's existing
  redaction contract, not a new architecture. Publication, review and merge
  remain pending; the broader effort and deployment holds remain open.
- The first review of #7708 found a missed Windows direct-handoff command
  echo that contradicted the documented count-only boundary. Four new native
  direct-launch fixtures first reproduced the exposure after confirming exact
  child argv and exits, then passed with a count-only handoff record. They
  execute the actual passthrough append and complete direct-launch block on
  both Windows shells with child exit zero and seven. The routine command
  logging scan found no remaining full-command echoes; child-emitted diagnostic
  and explicit runtime-path evidence are unchanged.

### 2026-10-21 - Detached Bash source slice merged; broader gates remain open

- #7697 merged as `087265dfd0fd311de8d80b547a7468c5a61bedf1` after
  Copilot approved exact corrected head
  `996a45dc711dfcb537b19063b795a7ea4d333242`, with no unresolved review
  threads and green current-head CI, including the required PR gate.
  The earlier compatibility observation was reproduced and corrected,
  not dismissed because the review footer reported no open issues.
- Re-read the Plan and Validation Plan after the actual merge and marked
  only detached Bash observation complete. Retained the published tip on
  a backup and advanced only fully merged history; no unpublished work,
  running installation or ownership state was discarded.
- Next independent presentation audit: early/late launch log records and
  routine raw-command diagnostics. Preserve native/protocol arguments and
  useful failure evidence without persisting prompt-bearing command lines.
  The context output adapter still awaits coordination on #6928; do not
  replace its contributor-owned watchdog or staging parent.
- Equivalent whole-flow costs, other standalone output owners and promoted
  installed qualification remain open. This source merge neither proves
  rollout nor authorizes another updater.

### 2026-10-21 - Detached Bash output owner and context-watchdog coordination

- The remaining PowerShell structured-context output is buffered by its native
  staging parent. Open contributor PR #6928 replaces that exact wait boundary.
  Read its current canonical watchdog and Worktrees caller, then coordinated on
  the original PR rather than opening a competing adapter. Deadline, identity
  and stage-retention ownership remain with that change; no context installer
  was edited here.
- The independent Bash detached branch already redirected native output, but
  bypassed framing and recorded no native completion. Its existing `setsid`
  launch now uses the shared formatter's explicit single-command observer.
  Literal arguments, installer-only staged environment, native admission,
  termination and the existing setup-log destination remain unchanged.
- Two real detached success/failure fixtures first failed against the old
  source because live progress was unframed. Both then passed, together with
  their blocking counterparts. The native Linux selection passed 57 cases;
  the separate finite-preflight selection passed six. Windows observer CLI
  and shared marketplace fixtures passed 21, with five unavailable-platform
  cases skipped. Original containment and item/group limits were retained.
- The observer preserves native exits, redaction, nested frames, UTF-8 and a
  partial final line, and stops at direct-child completion rather than waiting
  for detached-descendant EOF. It adds one observer per detached installation,
  not a process per line. Duration includes observed invocation and drain;
  these fixtures do not claim update savings, full package provisioning or
  promoted installed behavior. PR #7697 is open; current-head review and merge
  remain pending.
- The first review identified a real newer-launcher/older-runtime compatibility
  gap despite its generic no-open-issues footer. Two native legacy-formatter
  fixtures failed before correction. The detached bootstrap now checks the
  optional API in-process, retaining the new observer when available and
  explicitly warning before executing the original native command once when
  unavailable. The focused Linux selection then passed 29 cases with 32
  Windows-only skips, including old API, missing telemetry, literal arguments,
  native exits, redaction and direct-child lifetime. No probe process, runtime
  update or second installer execution was introduced.

### 2026-10-21 - Detached Windows sink merged; retain the inner output owners

- #7683 merged as `9b85465884d18fe14d40bdf2d52d7547016dd1d5` after
  Copilot approved exact head `314883097265f075e529f0eeff909a0e38e4ff9d`,
  no unresolved review threads and green current-head CI, including the
  required PR gate. The earlier review's startup-rotation concern was
  reproduced and fixed rather than ignored because its footer said
  "No open issues."
- Re-read this effort's Plan and Validation Plan after the actual merge.
  Marked only the finite detached Windows sink complete. The current source
  tip was retained on a backup, then only confirmed merged history was
  advanced; no unpublished work, runtime state or ownership record was
  discarded or repaired.
- Next output-owner trace: the structured-context installer's retained
  native self-staging parent. Compare the Bash inherited-output route with
  Windows' buffered captured child before choosing a progress adapter.
  Preserve their validation, staging, watchdog and publication ownership,
  and coordinate any overlap with existing native-installer work.
- Other standalone and early/late streams, banner convergence, equivalent
  whole-flow cost measurements, promoted installed qualification and
  authorized rollout remain open. This source merge does not change any
  deployment hold or authorize an installed update. The effort and source
  worktree remain active.

### 2026-10-21 - Close the detached sink's startup rotation gap

- The first #7683 review identified a real gap between the launcher's empty
  sink preparation and the observer's sharing handle. Rotation in that gap
  could remove the file, making the observer exit before running the
  installer even though the parent had reported a successful spawn.
- Added a deterministic native consumer regression that deletes the exact
  prepared sidecar at the actual detached-spawn seam. Both success and
  exit-7 scenarios first reproduced the missing progress/installation.
  Reuse the same fixture's argument, environment, hidden-console, output,
  active-rotation and shutdown assertions rather than creating another
  installation path.
- The observer now atomically opens or creates its own sink before invoking
  the original installer. Its existing sharing handle still excludes
  deletion during the invocation; no retry loop, parent wait, new authority
  or containment change is needed. All nine affected detached/startup native
  cases passed with the original limits. Source review, current-head CI and merge
  remain required for this pending boundary.

### 2026-10-21 - Give the detached Windows installer an owned output sink

- Traced the original `conhost --headless pwsh -File` launch: its console
  output had no explicit persistent sink. The legacy Worktrees installer
  already has an admitting human-output parent, but structured-context
  installation has a separate native validator and staging parent. Do not
  put either under a new installation executor merely to capture output.
- Reuse the launcher's native command observer and formatter in one
  detached PowerShell observer. It invokes the same original installer
  command with literal arguments and the inherited staged environment;
  admission remains before the detached spawn, and the installer retains
  ownership, staging, deadlines, execution policy and publication decisions.
  No Job breakaway or new deployment authority is added.
- A unique UTF-8 sidecar beside the main setup log receives start, physical
  progress lines, the native exit and the observed invocation's duration.
  The main log links that path. Separate files avoid new concurrent
  appenders; a read-sharing handle protects the active sink against log
  rotation until the observer exits. Existing canonical frames pass through
  once. One observer per
  detached invocation is presentation overhead, not a speed reduction.
- Native Windows fixtures exercise successful and exit-7 installers from
  both PowerShell hosts. They prove the launcher finishes before releasing
  the installer gate, early output is persisted, literal quoted arguments
  and non-ASCII content survive, the native descendant's console is hidden,
  and the observer's headless console exits after completion. Missing
  sinks, oversized Windows command lines and spawn exceptions are explicit
  startup failures. Existing synchronous action outcomes, fresh planning,
  refusal paths, quiet/stdio status sinks and deterministic clocks retain
  their original test limits.
- These are fresh disposable log/runtime-shaped native fixtures, not a
  clean-room package installation or a promoted live update. No installer
  construction or governance algorithm changes in this slice; a Windows
  `conhost` contract has no equivalent external Linux lane. Source review,
  required CI and merge still gate this pending boundary. Promoted installed
  qualification and authorized rollout remain open and unchanged.
- Structured-context staging still buffers its own captured child output;
  the detached sink measures arrival at the actual outer owner, not an
  invented execution cost. Its internal progress boundary, Bash detached
  streams, remaining early/late records, banner convergence and equivalent
  whole-flow cost measurements remain follow-on work.

### 2026-10-21 - Finite preflight slice merged; retain the remaining boundaries

- #7672 merged as `b4c46f4b23f668345e39b0005690881b7b059bea` after
  Copilot approved exact head `20cd77143697a9b1952356152b6bcb7f58c8f5e6`,
  no unresolved review threads and green current-head CI, including the
  required PR gate. An earlier approval addressed the preceding head and
  was not reused for the final correction.
- The final native regression distinguishes each failed child's exit 7
  from the unchanged aggregate preflight return 1. The scope completion
  reports that aggregate result, while each attempt retains its own
  diagnostic. All six native combinations passed with both contracts
  asserted and the original limits unchanged.
- Re-read the Plan and Validation Plan after the actual merge, then marked
  only this finite log boundary complete. The effort and source worktree
  remain active. Pull-forward retained the exact merged source on a backup;
  the installed sync helper refused a deleted PR branch during routine
  reconciliation, so the confirmed clean, fully merged tip was advanced
  without replaying or discarding unpublished work. No runtime update or
  coordination-record repair was used as a workaround.
- Next output-owner trace: detached marketplace-installer streams and their
  persistent-log destination, keeping detached lifetime, admission and
  background failure reporting separate from this finite preflight owner.
  Other early/late records, banners, equivalent whole-flow performance,
  promoted-source qualification and authorized rollout remain open.

### 2026-10-21 - Bound presentation to each post-maintenance preflight

- Traced both late runtime-preflight callers after the maintenance renderer
  has stopped. Their successful/failing/retried setup-log records used the
  legacy raw sink, even when the preflight's own stderr already supplied
  canonical progress.
- Reuse the existing phase-owned renderer for one finite preflight at a
  time. The preflight scope includes its attempts and retry waits; its
  completion is drained before returning. Runtime re-resolution, maximum
  three attempts, retry delay, native argv, captured stdout, optional stderr
  passthrough and the existing final continuation warning remain unchanged.
  No helper survives into the Picker or interactive session.
- An exact-source native Linux fixture passed six real Bash combinations:
  immediate success, third-attempt success and exhausted retries, each with
  captured or passed-through stderr. It verifies one renderer, no per-line
  clock process, UTC/monotonic setup-log framing, credential redaction,
  unchanged stdout/return behavior, and absence of live jobs or spools.
  The existing native Bash preparation/maintenance group passed 36 cases
  with two platform skips; no test was dropped or deadline raised.
- These are isolated native fixtures, not a full fresh-machine installation
  or a live rollout. No Windows producer changed; an actual macOS Bash host
  was not available for this source qualification. Existing renderer-failure
  and ownership-fenced shutdown regressions remain covered by the native
  preparation group. Hosted review and CI remain required before landing.
- Other early/late launch records, detached installers, banner convergence
  and equivalent whole-flow cost measurements remain explicit follow-on
  work. One bounded helper per finite preflight is presentation cost, not
  evidence that the complete update flow became faster.

### 2026-10-21 - Reconcile the merged standalone slice and continue the output audit

- Confirmed #7119 merged on 2026-10-19 as
  `c335e85435df6f15cd386bc39ce5f63b8fe98f2d`, with final source head
  `8acd1007f7a0dc35c3d99507c35a8c752b41913f` approved by Copilot.
  The landed slice includes the child-exit sample before Windows Job
  cleanup, completion after cleanup/drain/lease verification, the merged
  installer declaration refresh, and synchronization of the newly adopted
  PR-owner installation-context consumer.
- Re-read this effort after confirming the provider merge. The standalone
  Worktrees boundary is complete at source; the combined detached/other
  standalone and early/late Bash item is not complete. Reconciled the
  already-journaled #6877 Bridge source merge without claiming an installed
  serving-state proof.
- Retained the exact merged source on a backup before pulling forward.
  The normal sync refused replaying squash-merged history and auto-aborted;
  after confirming no unpublished commits or working-tree changes remained,
  advanced only that confirmed merged tip and completed the managed sync.
  No unmerged work was discarded and this active effort was not finalized.
- Next slice: trace the remaining Bash launch-log boundaries around the
  bounded maintenance renderer, preserving its phase-owned lifetime,
  machine-stream isolation, redaction and monotonic timing contract.
  Banners, equivalent whole-flow cost measurements, other native producers
  and promoted installed acceptance remain open. No update, production
  recovery, seed delivery or deployment-hold change was performed.

### 2026-10-17 - Keep standalone build guards independent of runtime imports

- The approved candidate's actual CI failed seven installer-engine guard
  cases because their standalone file loader still addressed the Worktrees
  compatibility shim after the capability implementation moved. An
  installed local runtime could mask that new import dependency; this was
  a regression caused by the move, not an unrelated failure to bypass.
- The guard now loads the shared stdlib-only capability source directly.
  A new real `python -I -S -B` regression first reproduced the missing
  `update_telemetry` import, then verified a real packaged declaration with
  neither runtime package imported. Existing declaration, rewrite, tamper
  and whole-payload assertions are retained.
- CI's merge tree contained newer canonical/materialized fixture coverage
  from #7124. The smaller pre-sync local result was not accepted as that
  matrix. Committed the repair, retained a backup, and used managed sync
  to pull forward 29 upstream commits. All follow-up patches remained
  equivalent; upstream fixture expansions were preserved, not copied away
  or dropped from this PR.
- The complete synchronized 47-case guard suite passed on Windows in
  73.39 seconds and on an exact-code native Linux snapshot in 4.21 seconds,
  including the new bare-interpreter proof. An earlier concurrent Windows
  attempt hit the unchanged 30-second item bound and recorded an 18-second
  filesystem scan; it is not a pass. Final validation ran serially, with
  no item/group/resource limit increase or fixture/assertion removal.
- Installer-engine, installation-context and payload generation checks
  remained synchronized after pulling forward. The prior approval applies
  to the old head; the repaired, synchronized candidate still owes fresh
  review, hosted CI and an actual merge before this slice is landed.

### 2026-10-17 - Classify unattributed-source diagnostics as warnings

- Review found the existing unattributed-source admission diagnostic was
  passed directly to generic progress, omitting the human warning state.
  The existing warning callback is now defined before lease admission and
  reused there; timeout/cancellation diagnostics and plain stderr retain
  their existing behavior. Regenerated both lifecycle consumers.
- A real-lease regression first reproduced the missing warning classification.
  It covers human and plain output with a real failed child, exact exit 37,
  one source diagnostic, retained unattributed pending ownership, unresolved
  selection and released lease. The plain variant uses file-descriptor
  capture so the native child's inherited streams remain real file handles.
- The complete stdio file plus the existing unattributed admission,
  requalification and explicit-policy refusal cases passed 18 Windows
  cases in 33.29 seconds. An exact-code native Linux snapshot passed
  eleven cases with four Windows-only skips in 8.19 seconds, including
  both new real-lease variants and the existing fresh-bootstrap/generation
  contracts; its separate compatibility-export import also passed.
  Original containment limits remain unchanged.
- Classification does not grant ownership or change mutation policy,
  child status, redaction or machine streams. Fresh hosted review and CI,
  followed by release and authorized running-state proof, remain open.

### 2026-10-17 - Preserve mixed-case PowerShell installer actions

- A fresh review accepted the real-CLI fixture correction but reported a
  previously missed Medium issue in its body despite displaying no open
  threads: PowerShell accepts mixed-case `Install` and `Update`, whereas
  the newly forwarded Python human-operation choices are lowercase.
  This observation was treated as blocking, not as a clean verdict.
- The extended native self-staging regression reproduced exit 2 for
  `Install` before admission. Normalize only the human-operation label
  with `ToLowerInvariant()`; the native child's original action and
  remaining argument vector are untouched. Regenerated the entrypoint-bound
  UTF-8 declaration after changing the installer.
- The complete self-staging matrix now includes `Install` and `uPdAtE`
  on PowerShell 7 and Windows PowerShell 5.1. Ten native cases passed
  with ten POSIX-shell platform skips in 100.22 seconds, retaining the
  original 30-second item and 300-second group limits. Stamp remains
  unframed, native staging and cleanup remain verified, and actual
  install/update output retains lowercase UTC/monotonic framing and
  pending ownership rather than claiming a built runtime.
- This one-line PowerShell correction does not change Bash's case-sensitive
  action contract or require another updater. Fresh current-head review,
  hosted CI, promoted release and authorized rollout remain separate gates.

### 2026-10-17 - Cover the standalone shared-library consumer in release metadata

- Hosted `guards + lint` failed the changefile-presence check: the
  `update-telemetry` source change also fans out to its Worktree Manager
  consumer, but this PR's pending changefiles covered only the eleven
  plugins. The narrower local plugin-only check missed that standalone
  consumer; it was not a passing substitute for the actual CI guard.
- Added the required Worktree Manager patch changefile through the existing
  changefile tool. No version was hand-edited and no runtime code changed.
  Publication is gated on the exact CI guard against its explicit base
  and the repaired committed head. The hosted check and fresh review
  remain required; this metadata repair does not waive either.

### 2026-10-17 - Restore complete runtime fixtures for real CLI dispatch

- Review of #7119 found three real-CLI cases incorrectly using the thinner
  dispatch-only fixture. Their earlier passing results could use an ambient
  Worktrees installation and are not accepted as isolated runtime evidence.
  Those three results are superseded, not defended as a speed optimization.
- Restored the complete copied runtime and materialized declared dependencies
  for active-context selection, provisioning and concurrent first use.
  Each case now explicitly requires its copied `__main__.py`; selection and
  provisioning also assert real version output, as single-flight already did.
  Dispatch-only fixtures remain confined to explicit stubs and pre-dispatch
  refusals, not real CLI execution.
- An initial complete-fixture attempt exceeded the unchanged 30-second item
  bound before context stamping and is not a pass. Bounded instrumentation
  measured fixture copying and native dispatch separately without changing
  implementation or safety checks. Final uninstrumented acceptance passed
  all three restored cases plus the existing no-site import-origin/version
  regression: four passed in 68.02 seconds. Individual calls took 16.82,
  17.96, 20.34 and 12.02 seconds respectively, below the original item bound;
  the 300-second group and resource limits are unchanged.
- The no-site case loads only the external YAML dependency explicitly and
  proves the copied Worktrees configuration and transport modules originate
  inside the staged payload before executing the real CLI. No import graph,
  version assertion or ownership/governance check was replaced by a stub.
  This repair changes Windows-only test fixture callsites, not production
  runtime behavior. Hosted review/CI and the remaining effort/rollout gates
  stay open.

### 2026-10-16 - Honor an explicitly disabled composed-status switch

- Review of #7119 found that the new PowerShell opt-in precondition tested
  parameter presence rather than its switch value. Explicitly splatting
  `WithContextValidation = $false` therefore rejected ordinary status
  without expected-context arguments and rejected unrelated actions.
- A real native regression first reproduced all four failures: ordinary
  status and source identity, each on Windows PowerShell 5.1 and
  PowerShell 7. The precondition now tests the switch value, matching the
  existing execution branch. All eleven vendored runners are synchronized.
- The complete native bootstrap file plus enabled composed-status and
  changed-receipt consumer regressions passed 18 cases in 77.49 seconds.
  Disabled calls emit the same parsed JSON as omission; enabled calls still
  require the explicit action/expectations and independently revalidate
  changed receipts. Original item, process and aggregate bounds remain.
- This PowerShell-only repair changes no Bash/Python runner or production
  installation. Native Windows entrypoint tests cover the changed contract;
  full fresh-machine construction and live rollout remain outside this
  source slice while the existing release and deployment holds apply.
  Current-head hosted review and CI must clear after publication.

### 2026-10-16 - Keep the lifecycle capability source plugin-independent

- Review of #7119 identified a real source-ownership coupling: generic
  payload generation read its capability probe from Worktrees. Moved that
  unchanged implementation into `update_telemetry.capabilities`; the old
  Worktrees module re-exports the same API. Generated bootstrap assets
  remain unchanged and require no installed runtime.
- A regression generates and verifies another eligible plugin's two human
  helper packages from a minimal source root with no Worktrees plugin.
  Runtime compatibility exports are the shared owner's actual objects.
  Windows focused generation, lifecycle and real producer contracts passed:
  32 passed, one platform skip in 57.30 seconds. Exact-snapshot native Linux
  bootstrap/generation coverage passed nine cases with four platform skips;
  a separate isolated import verified every compatibility export against
  that snapshot's shared source. Original containment limits are unchanged.
- The additional Linux Worktrees test-file attempt could not collect in the
  available Dispatch test environment (missing `machine_transport`); the
  existing Worktrees environment has no pytest. Neither attempt is a pass.
  The actual relocated import contract was instead exercised directly in
  the contained snapshot process; the full native producer matrix above
  ran in the available Windows Worktrees environment. No dependencies were
  installed or production runtimes modified to obtain this evidence.
- All 13 generated manifests, Python lint and the install-contract gate
  passed. This is a source-only review repair; required current-head review
  and CI must clear again after publication. The remaining presentation,
  whole-flow cost, promoted-release and parent-launch gates stay open.

### 2026-10-16 - Reuse native types, never governance results

- A private two-caller trace found the first caller spending 7.6 seconds
  in initial governance and another 11.3 seconds in fresh pre/post checks;
  the second waited 12.3 seconds for the provision lock, then entered its
  own fresh check. The lock was already released before CLI execution.
- Invoke the canonical runner in the launcher's existing PowerShell host,
  in an independent child script scope on every check. This retains only
  native helper types; receipt hashes and all governance reads reset on
  every invocation. Both native hosts prove the same dispatcher PID runs
  two or three real checks as appropriate, and actual policy changes at
  either provision boundary still refuse dispatch with exit 126.
- Thirteen focused native cases passed with unchanged deadlines. The
  original two-caller case passed in that lane and in the later complete
  file prefix. The complete file then exceeded its 300-second aggregate
  budget, not an item deadline; complete acceptance now requires both
  documented complementary generator partitions, never a selected pass
  alone. No case or assertion is removed and no limit is raised.
- A separate full-graph import trace measured 8.687 seconds importing,
  versus 6.078 seconds without unused disposable bytecode writes.
  Worktrees fixtures now suppress those writes, retaining all real
  imports, version checks and staged-dependency origin assertions.
- Current source acceptance now covers all 111 Windows generator cases
  through the two complementary partitions: 96 passed and 15 platform
  skips. The larger partition completed in 279.44 seconds; real staged
  imports took 22.94 seconds and original concurrent first-use 20.88
  seconds. No timeout or missing case is counted as a pass.
- Complete Windows lifecycle/output and binding file groups passed
  separately (54 passed/12 platform skips and 28 passed/6 platform skips).
  A native-filesystem Linux snapshot of the same candidate passed all
  four files together: 139 passed/66 platform skips in 110.08 seconds.
  Canonical generation, installation-context and installer-engine adopter
  checks, Python lint and the install contract passed. An independent
  staged-diff review found no significant issues.
- The clean-room contract tier exercises actual isolated admission,
  self-staging and subprocess boundaries on both native hosts; it is not
  proof of a full installed runtime construction. The hardened-container
  attempt remains blocked before tests by its metadata TLS failure.
  Production rollout is not attempted while existing deployment holds
  remain. Hosted PR review/CI, release and parent completion gates stay open.

### 2026-10-16 - Attribute native governance cost and retain fresh validation

- A fresh synthetic status trace attributed 4.814 of 10.059 seconds to
  146 canonical path calls. Native final-path resolution itself took only
  about 60-90 milliseconds across those calls; repeated provider existence
  probes, not the final-path safety primitive, dominated that part of the
  work. Uncached native positive probes retain the original provider check
  for negative/error cases. A subsequent source status measured 4.109
  seconds, with no ownership or receipt checks removed.
- The Worktrees Windows dispatcher now requests status plus its independent
  expected-root/plugin context validation in the same interpreter. Validation
  still runs fresh after status, and every initial, pre-provision and
  post-provision generation comparison remains. Default status JSON is
  unchanged. Both Windows dispatch branches release the provisioning lock
  before running the CLI, matching the existing POSIX lifetime.
- Nine focused native cases passed under the unchanged item/suite limits,
  including the original two-caller single-flight case. PowerShell 7 and
  Windows PowerShell 5.1 both proved composed/separate result equivalence,
  refusal after a receipt changes between the two reads, and actual runtime
  access to the released lock on the locked-fast and provisioned paths.
  Native existence coverage separately proves literal paths, directories,
  deletion without stale caching, and provider-error propagation.
- Complete Windows generator acceptance is still blocked: a broader run
  passed 37 cases before the copied full-dependency import fixture exceeded
  its 30-second item limit while materializing a canonical dependency.
  That case passed separately in 11.29 seconds (5.813 seconds copying,
  0.938 materializing and 5.078 importing); the discrepancy is not waived
  as pre-existing. A diagnostic replay of the eight new native cases plus
  the complete copied-runtime import passed all nine in 112.67 seconds;
  dependency materialization itself took 2.484 seconds in that replay.
  The fixture inventory identified 422 test files plus 42 documentation/skill
  files being copied despite not being consumed by these runtime contracts.
  Those authoring/test files are now excluded from the Worktrees fixture;
  its runtime sources, complete materialized dependency graph, and real
  import/version assertions remain. Keep publication blocked until the
  complete original-deadline generator lane is green. Installed
  rollout, other output producers and the parent completion gates stay open.
- A later broader run passed 42 cases, including the complete dependency
  import, but again timed out on first-use. Inspection found the containment
  monitor repeatedly rescanning the growing fixture corpus with three
  path-stat operations per file. On the retained completed sandbox, the
  original walk took 2.750 seconds; a fresh `scandir` walk took 0.141 seconds
  and returned the identical 69,613,770 bytes across 4,342 files. The old
  scan could exceed its own two-second sampling cadence and continuously
  compete with the tests it monitored.
- Replaced only that metadata enumeration, retaining fresh walks, symlink
  exclusion, the sampling interval, and every resource limit. Native tests
  still terminate a real over-budget child tree. That test, nested-file and
  link accounting, all native bootstrap/argument guards, and the current
  lifecycle output suite passed together: 26 cases in 69.68 seconds.
  The next complete generator run passed 43 cases, including copied-runtime
  import and original first-use, but the two-caller case still exceeded its
  30-second item limit in that accumulated run. Its focused passing receipt
  does not close this discrepancy. Attribute both callers' remaining phases
  before another implementation or unchanged suite retry. None of these
  results is an installed-runtime or rollout receipt.

### 2026-10-16 - Standalone admission-parent candidate and remaining probe cost

- Added explicit human-output opt-in to the existing Worktrees admission
  parent for standalone `install` and `update`. Bootstrap formatter,
  redactor and encoding-capability assets are generated from their existing
  canonical sources; rendering starts no additional interpreter. Default
  lifecycle streams, arguments, lease ownership and strict Job cleanup
  remain unchanged. Stamp, teardown and protocol callers do not infer human
  mode from output or ambient state.
- Native Linux validation of exact staged tree
  `25e199e6e3179459d4e54f3ffdeaf3b532c9a84b` passed 116 lifecycle,
  output and generator cases with 43 platform skips, on native storage
  under the existing limits. Windows output coverage passed 12 cases;
  separate real admission/self-stage cases passed on both PowerShell 7
  and Windows PowerShell 5.1, including strict human-line grammar.
  These are bounded fresh synthetic consumers, not installed rollout.
- Broader Windows generator acceptance is not green. A stale isolated
  test dependency was repaired through the normal test-environment
  preparation flow, but the real namespaced first-use case still exceeds
  its unchanged 30-second item deadline. Native boot tracing attributes
  roughly 15 seconds to initial resolution/validation and another
  11 seconds to the pre-provision governance recheck, before the required
  post-provision recheck. Cold native-type initialization alone measured
  only 1.301 seconds in PowerShell 7 and 1.152 seconds in Windows
  PowerShell; it is not established as the dominant cause.
- Keep publication blocked while attributing that remaining cost. Fixture
  experiments must not replace the real governance checks, drop first-use
  coverage or increase deadlines. Other standalone/contextual producers,
  early/late Bash output, whole-flow cost measurements and authorized
  promoted installation remain open. No production update or admission
  change is implied by this source candidate.

### 2026-10-16 - Remove unnecessary UV acquisition on admitted reuse

- Merged #7054 as `be1ab20e995845c78454b49b73b21de62d782867` after
  current-head approval with no findings and successful CI. Pulled the
  continuing worktree forward; this is source completion, not installation.
- Moved Codespaces PowerShell UV acquisition after canonical admission and
  its healthy-reuse return, matching Bash without changing either safety gate.
  The real acquisition helper now performs zero UV subprocesses on reuse.
- Added a real first-touch construction-owner witness, required acquisition
  failure and canonical abort/reclamation regression. It failed before the
  fix and passes afterward. Current/refused runtime bytes remain unchanged.
- Final native Windows selection: 11 passed, 10 platform skips; native Linux
  parity: 10 passed, 11 platform skips. Original bounds remain unchanged.
  Regenerated only the owning native encoding digest; install-contract,
  synchronization, lint and diff checks pass.
- Independently confirmed the cooperating Bridge source slice #6877 merged
  as `62b5c976a385a904e53b53d55f78ea8b62c14450`. Its rollout and receiving
  coherence remain separate from source completion. No installed update ran.

### 2026-10-15 - Exercise Codespaces reuse through canonical admission

- Landed #6940 as `298fbd475b2a265e91b3e0d3ff31aedf2da2343e` after
  current-head approval with no open findings and successful CI. Addressed
  review by qualifying fresh-venv abort evidence as Bash-only and recording
  the required documentation-impact statement.
- Added isolated native coverage of the existing #6598 venv/package reuse
  path using a real Python venv, real module-launch health and the actual
  attached canonical construction/retention adapter. Healthy reuse avoids
  venv creation, package installation and build stamping.
- Unhealthy entrypoints, conflicting same-version payloads and malformed
  completion markers retain their precise refusal reasons and preserve the
  tested runtime files and selector bytes/timestamps. This is not a stubbed
  admission result or a production activation.
- The four-state matrix and existing first-construction retry case pass five
  cases per native host. The audit records validation boundaries and the
  Linux pytest configuration warning explicitly.
- Observed one remaining PowerShell-only UV-acquisition helper invocation
  before the admitted-reuse return; Bash already avoids it. The production
  scripts remain unchanged pending native-file ownership clearance.
- Promoted-payload and installed reuse proof, full provisioning, real
  construction/cutover costs and complete standalone presentation remain open.

### 2026-10-15 - Verify independent final-integration work counts

- Landed the bounded test-and-audit slice in #6933, merge
  `29259f83217bf82e1c091ebadefc143964a94094`, after current-head Copilot
  approval with no findings and successful CI. No production implementation
  change, installed update or service operation is part of this slice.
- While shared standalone-presentation files remain reserved, verified #6836's
  existing reduction without editing its implementation or running an updater.
  The native Windows fixture observes two command-reconciliation callbacks
  without deferral and zero with it; aggregate update retains exactly one
  final command pass and one final profile pass.
- Extended that aggregate count contract to first touch with no deployed
  version. Current, changed and missing runtimes retain the same late
  finishing order; only changed/missing runtimes invoke the engine installer.
- Both complete orchestration/integration modules passed 42 cases on each
  of Windows and native Linux; six native PowerShell deferral/verified-result
  cases and two native Bash count cases passed. The audit
  separates these measured invocation counts from test-run durations:
  neither is installed end-to-end speed evidence.
- The Linux run exposed a Windows-simulating test that still discovered a
  real PowerShell executable despite mocking all child execution. Bound that
  discovery to a synthetic fixture command; both native suites now pass
  without requiring PowerShell on Linux.
- Standalone presentation overlap, real runtime construction/cutover
  measurements and promoted rollout proof remain open.

### 2026-10-15 - Standalone presentation-owner decision

- The operator selected the existing Python admission parent for remaining
  standalone human presentation, rather than adding a reporting interpreter.
  Reuse `HumanOperation` and the existing live native relay with an explicit
  install/update opt-in and source-owned packaged support.
- Default lifecycle stdout/stderr identity, parsed protocols, execution policy,
  exact native argv, admission and child-lifetime behavior remain binding.
  A runtime package that has not been built yet cannot supply bootstrap
  presentation support.
- Structured-context native installers bypass legacy command admission.
  Their actual output owner must be addressed separately; a legacy-parent
  change alone cannot establish every-line standalone coverage.
- Requested the exact reserved-file overlap from the existing source owner
  before changing shared lifecycle, generation or native installer support.
  No such implementation change is included in this journal entry.

### 2026-10-15 - Attribute the next normal-release blocker without a waiver

- The latest inspected normal promotion, run `38841372088`, validated
  `aaace382f7b9b45ee0a9c47ee2da9f72b7348b30`. Only
  `full - agent-worktrees` failed, job `116782077335`.
  Fourteen test groups passed before the plugin's existing aggregate budget
  expired in group 15, which had only `17.9934s` remaining. This is not one
  of the earlier assertion failures repaired by #6910.
- The deterministic suite contains 402 files in 17 groups. The inspected run
  spent approximately 122s, 162s and 120s in groups 6, 7 and 10 respectively.
  Those are whole-group observations, not proof of which individual tests
  caused the excess. Public coordination is recorded on #6812.
- Local runtime profiling is not yet evidence: the existing interpreter
  lacked `machine_transport`; canonical dependency preparation on the
  mounted checkout exceeded a bounded wait. No surviving owned preparation
  child was found afterward.
- A fresh exact-source Linux filesystem fixture exercised the preferred
  hardened devcontainer wrapper with already-provisioned tooling. Its normal
  dependency-preparation pass failed fetching package metadata with TLS
  `HandshakeFailure`; the network-disconnected test pass never started.
  No verification, seasoning, network or resource control was disabled.
- No test was removed, timeout raised, pipeline retried/cancelled or updater
  started. Promotion and the installed proof remain blocked; source-group
  runtime attribution remains open rather than being called a known flake.

### 2026-10-15 - Synchronous output slice merged; retain the parent gates

- #6920 actually merged as `bf3d5a03792ccfb4fe4e92b98d7e894de4b83119`
  at `2026-10-16T03:38:42Z`, after Copilot approval of exact head
  `a21d89aa1e1d074c3042c157089d82c6579e0e86`, zero findings and
  successful observed checks including the required aggregate gate.
- Native PR state now reports merged, and supported worktree synchronization
  advanced to that merged commit without conflicts. Recovery refs remain.
- Re-read the Plan and Validation Plan after merge. Only the synchronous
  bootstrap/blocking marketplace output item closes here. This is not a
  standalone-installer guarantee, runtime speed proof, promoted release
  receipt, installed verification or effort completion.
- Continue at the remaining output owner's native entrypoint/admission
  boundary. Preserve independent installer lifetime, execution policy, exact
  command and ownership behavior; do not invent a second updater to obtain
  timing. The effort remains Active and the source worktree remains retained.

### 2026-10-15 - Synchronous bootstrap and marketplace boundary

- Traced the actual pre-launch producer to direct native installer argv.
  Its admission subprocess inherits streams and is not a Python human-output
  owner, so the launcher is the smallest missing presentation boundary.
- Reused native reconciliation's PowerShell runner for bootstrap updates and
  the blocking marketplace installer: exact argv, scoped environment,
  Stopwatch duration, streamed diagnostics, explicit spawn/exit outcomes and
  restored native-error preferences. No per-line subprocess was introduced.
- Bash uses its existing maintenance renderer for the same actions, preserves
  final unterminated output and canonical nested frames, and records the
  installer's own pipeline exit status instead of the logger's.
- A fresh complete blocking fixture exposed empty optional stage fields
  shifting Bash's tab-delimited metadata. Replaced that handoff with NUL-delimited
  fields in the same existing producer call; the fixture validates the real
  decoded installer path and runtime-root arguments.
- Before the fix, both Windows bootstrap hosts leaked raw child output and
  failed the streaming gate; native-error policy could abort the launcher.
  Updated fixtures cover failed, missing, successful and unchanged actions,
  metacharacter argv, both streams, nested frames, final lines, environment
  restoration and fresh second-pass planning.
- Targeted Windows native bootstrap/reconcile: eight passed, four POSIX skips.
  Blocking marketplace: four passed, two POSIX skips. Native Linux changed
  boundaries: six passed, twelve Windows skips. Existing 30-second item,
  20-second child and 300-second contained-suite bounds are unchanged.
- This source slice is not yet published. Detached/standalone output, early/
  late Bash diagnostics, speed reductions and promoted installed proof remain
  open; this is not effort completion or production rollout evidence.

### 2026-10-15 - Native maintenance slice merged; continue remaining streams

- #6904 actually merged as `aaace382f7b9b45ee0a9c47ee2da9f72b7348b30`
  after fresh approval of exact head
  `20c6573798425bdcb5f1cd0fe956f78af1c0948d`, no unresolved review
  threads and all observed CI successful. The source review's lifecycle
  and timing findings were reproduced and repaired, not bypassed.
- This closes the native maintenance formatter/sink extension. It does not
  close inherited bootstrap output, detached/standalone installer output,
  early/late Bash diagnostics, banner consolidation, Bridge serving drift
  or any promoted installed proof. The next slice traces those streams at
  their existing output owners rather than adding per-line interpreters
  or another executor.
- Native worktree status reconciliation encountered a busy publication
  lock after the confirmed merge. No lock or tracking record was forced.
  Preserve source history and journal, then retry the supported status/sync
  path after publication finishes; this active effort must not be finalized.
  The later status retry confirmed merged state. Whole-branch sync safely
  aborted on the already-squashed history; after retaining backups and
  verifying the native payload was byte-identical upstream, replayed only
  the unpublished journal. The supported sync then completed successfully.
- Separately attributed the normal release's three Agent Worktrees
  failures to existing #6907, #6208 and #6209. Their existing owner already
  landed #6910 (`f9093a58fffe088f232746c9f028c41ce5c2976a`), independently
  confirmed merged. Do not duplicate that fix or treat its dev merge as a
  containing full release. No updater or new production activation ran.

### 2026-10-15 - Native maintenance acceptance and isolated Windows blocker

- Further review found successful jobs could already be reaped before
  cleanup, and a blocked terminal could keep an orphaned renderer alive.
  Both were reproduced before their repairs. Final cleanup now requires
  an explicit drained/closed receipt and absence of a live owned job,
  rather than relying on retained Bash exit status. The parent monitor
  starts before output, checks liveness during admission, remains active
  through final drain and can terminate only its dedicated CLI process
  without waiting for sink locks. A private failure reason survives.
  Native regressions keep a pipe open and unread while the actual parent
  exits, covering both a blocked start and final status/drain. Both now
  exit; Linux pidfds constrain fixture cleanup to its exact grandchild.
  Final complete-file acceptance passed 113 telemetry and 22 preparation
  cases on Linux (12 Windows-only skips); canonical Windows telemetry
  passed 111 cases (two Linux-only skips). The equivalent final 256-line
  comparison measured medians of 3.724s before and 2.154s after, with
  256 versus zero `date` processes, one formatter and all messages retained.
  These replace all earlier overhead figures, not runtime/update cost proof.
- A subsequent review identified queued output inflating native action
  durations. A blocked-sink regression failed before the repair: completion
  could not be sampled until that sink was released. One in-process clock
  reader now observes end records independently; action admission starts
  its clock after prior output drains. Only boundary timestamps are queued
  in memory, while diagnostics retain the existing private spool.
  The repaired regression distinguishes a two-second action from an
  additional 88 seconds of simulated drain delay. Spool initialization
  failure is also explicitly reported without aborting native continuation.
  Windows shared renderer coverage passed 30 cases; complete native Linux
  telemetry and preparation files passed 111 and 21 cases respectively
  (12 Windows-only skips), retaining the original limits.
  Repeated the equivalent 256-line comparison at that clock-repair stage,
  including clock-reader and job-shutdown costs: before 3.435s median,
  after 1.900s, still 256 `date` processes versus none and one formatter.
  All diagnostics remain; these supersede the pre-review overhead figures.
- Review of #6904 identified cached-PID shutdown targeting. A contained
  pre-fix regression reproduced signalling a disposable unrelated process.
  Shutdown now verifies the parent shell's renderer job, command and PID
  before signalling or waiting, including completed-job cleanup. A separate
  regression preserves a different job even when its numeric identity is
  supplied as the stale renderer handle. The complete native Linux
  preparation file passed 20 cases with 12 Windows-only skips afterward;
  stopped/missing helpers and normal spool cleanup remain covered.
- Native PowerShell 5.1/7 progress and real output-sink fixtures passed on
  Windows: 26 cases with two POSIX skips. Native Linux engine/library and
  Manager coverage passed 128 cases with 12 Windows-only skips, including
  helper death, bounded shutdown, unchanged planning and streamed failure.
  Focused Windows shared-renderer coverage passed 29 cases.
- Equivalent 256-line synthetic presentation trials, including helper
  startup/shutdown, retained every diagnostic. The median fell from
  3.784s with 256 `date` processes to 1.252s with one bounded renderer and
  no per-line clock process. These measure presentation overhead only,
  not installer or end-to-end update savings.
- Broader Windows pane coverage exposed a separate unchanged error-output
  boundary. Instrumentation attributed about 13.272s to `Write-Error`,
  not the roughly 29ms exclusive-file failure. A minimal unrelated
  `pwsh -File` script also exceeded its existing 15s deadline under
  ConciseView; its NormalView counterpart completed in 2.453s. Track
  the actual PowerShell error-rendering cause in #6900; do not weaken
  deadlines, change global ErrorView or claim that the broader lane passed.
- A prior broader run also detected persistent OneDrive environment drift.
  Causality is unestablished on the shared host; no rollback or production
  repair was attempted. Subsequent isolated timing probes detected no drift.
- Fresh synthetic native consumers exercise the changed boundary.
  Full fresh-machine bootstrap and an actual macOS Bash 3.2 host were not
  available; Linux Bash execution does not substitute for that platform.
  No live rollout was attempted: promoted artifacts and sole-owner update
  authorization remain required. Required PR review and CI remain gates.

### 2026-10-15 - Formatter merged; continue native presentation and speed proof

- #6869 merged as `85eb63693a071f22f607a16063fd4be3044d75d4`
  after current-head approval with zero findings and all observed CI
  checks successful, including the required PR gate. This lands only the
  bounded Python-owned presentation slice, not this whole effort.
- Its source was pulled forward onto the contribution branch without
  dropping unmerged changes or bypassing publication checks. The original
  PR rebase and final accepted tree remain recoverable through local refs.
- Notified the retained #6826 source owner that the actual-merge dependency
  is satisfied. #6877 still owes synchronization, owner-tool declaration
  regeneration, resulting-head review and all CI; production rollout and
  native-recovery custody remain separate.

### 2026-10-15 - Portable native maintenance clock decision

- The operator selected one bounded formatter helper per Bash maintenance
  phase rather than removing Bash 3.2 support or pretending its `SECONDS`
  variable is monotonic. The helper is stopped before Copilot handoff; no
  per-line Python/date spawn, daemon or lifecycle authority is introduced.
- The native slice reuses the shared formatter, continuation redactor and
  human output grammar. PowerShell renders natively with UTC and Stopwatch;
  Bash retains command execution and delegates presentation only.
- Standalone/detached and inherited bootstrap installer streams, plus
  early/late Bash launcher diagnostics, remain explicit follow-on scope.
  Native-shell tests and
  measured overhead must clear before publication; this is not a rollout
  or whole-effort completion.
- Next: finish native shell/standalone and persistent-log timing through
  their actual output owners, starting with
  `worktree-manager/bin/launch-session.{ps1,sh}`. Preserve Bash 3.2,
  monotonic-duration honesty, machine streams, native argv/exit behavior
  and the absence of per-line subprocess or telemetry-write overhead.
  Do not re-audit the already-accounted 249-line reference or rebuild the
  already-merged Python formatter.
- No installed update has been run on this evidence. Promotion, sole-owner
  rollout, equivalent before/after cost measurements, Codespaces reuse
  verification and the parent launch's acceptance gates remain open.

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
