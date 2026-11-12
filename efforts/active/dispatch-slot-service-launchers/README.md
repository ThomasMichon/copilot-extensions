# Dispatch Slot Service Launchers

## Goal

> The file lock implies this binstub improperly gets held open instead of quickly dispatching into a version-slot binstub. The agent-dispatch incoming setup flow should be entitled to lock-break, usually be killing the outgoing process, if this situation occurs

The requested source behavior is a short-lived stable service entrypoint,
version-slot execution, and an incoming installer's bounded ability to retire
an outgoing central-service process that blocks replacement. The observed
exception alone does not establish its handle owner. Installer-owned readers
must close; retiring another process is not a substitute for that correction.

- **Slug:** `dispatch-slot-service-launchers`
- **Repo:** copilot-extensions
- **Branch(es):** separate proposal and implementation PRs against `dev`
- **Created:** 2026-11-11
- **Status:** Active
- **Vision:** [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11875
- **Related ownership:** #11960 reader disposal; #10847 central retirement;
  #10377 upgrade recovery; #5579 wrapper lifetime

## Guiding Intent

Close existing authoritative replacement intent, not introduce another
installer, service manager, reaper, scheduler or generic process-killing command.
Known generated stable launchers must not retain an open file handle for the
service lifetime. Long-lived coordinator and supervisor execution belongs to
the selected immutable runtime generation. Applicable entrypoints must select
the incoming ready generation, while outgoing execution and protected workers
retain their own explicit lifetime and state.

An incoming setup may resolve a real publication conflict through the existing
bounded central-service replacement owner. Preserve native process identity,
installation scope, explicit service role and then-current generation checks
at the termination boundary. Unknown/foreign holders, session processes,
current or reselected generations and later successors are not targets.
A real unresolved OS publication failure remains a failure, not a false
successful installation or an indefinite predecessor-recovery wait.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launcher driver | Proposal, distinct launcher-lifetime implementation, contained qualification and PR stewardship | Managed source `polished-kiln`; #11875 |
| Reader-fix contributor | Existing first-line reader disposal and its native qualification | #11960; ownership retained |
| Lifecycle owners | Existing central generation retirement and state-preservation contracts | #10847 and the installer contract; no transfer |

## Coordination

- **Topology:** reviewed proposal first, then a separate bounded implementation.
- **Host:** Launcher driver owns this effort and its source PRs only.
- **Delegates:** none.
- **Overlap:** do not duplicate, replace, merge prematurely or supersede #11960.
  Keep its reader correction and native validation hold with its contributor.
  Leave its ownership-reader block, regression tests and changefile untouched.
  Develop the separate generated-body/slot-execution changes without importing
  that contribution; check its exact patch still applies and retain its native
  hold. An actual conflicting hunk requires reconciliation with its owner,
  not a competing reader correction.
- **Handoff:** this README, #11875, exact source/test evidence and outstanding
  dependencies; a plan or source merge is not rollout.
- **Parent:** this is a prerequisite slice, not ownership of the wider
  PR-service, provider-conformance, upgrade-recovery or retirement campaigns.

## Context

Dispatch's generated `serve-service.ps1` and `supervise-service.ps1` contain
runtime selection and long-lived invocation. A changed-content refresh can
fail in the atomic publisher after a coordinator cutover. The unchanged-byte
optimization does not prove that changed bytes can publish.

There are two distinct boundaries: the installer's first-line reader may itself
retain a handle (#11960), and an outgoing service wrapper may hold stable code.
Do not label either as the observed holder without native evidence.
Reuse [Windows scheduling](../../../plugins/agent-dispatch/docs/windows-scheduling.md),
the [installation contract](../../../docs/install-contract.md#authoritative-replacement-and-completion)
and [graceful cutover](../../../docs/patterns/graceful-daemon-cutover.md).

## Request

The Goal preserves the complete public-safe operator request verbatim.
Implementation was paused for unavailable Git tooling; a subsequent
"Check status here and resume" resumes this source assignment after tooling
restoration. It does not erase the earlier host update's failed outcome or
authorize another live update.

The subsequent instruction "Reconcile gainst it" selects reconciliation against
the current #11960 patch rather than waiting solely because both contributions
touch the installer. Preserve the original contributor's expression and hold;
this is not authority to merge, replace or waive that contribution.

## Scope Approvals

- Source development, disposable caller-contained native fixtures, ordinary
  reviewed source publication and release observation are in scope.
- No shared service termination, installed-runtime patch, host repair,
  configuration/credential change, update retry or rollout is authorized here.
- A review accepts source design, not new host or product effects.
- Preserve the existing reader contribution, independent native holds, original
  parent acceptance and resources; neither this slice nor a phase closes them.

## Plan

The following decomposition is **agent-recommended**, not additional operator
wording or authority.

### Phase 1 - Review and establish the real lifetime boundary

- [x] Review this proposal and reconcile intersecting ownership with #11960.
- [x] Trace actual coordinator and supervisor entrypoints, slot retention,
  scheduled-task behavior and existing native retirement primitives. Record
  which reader/process holds a deny-delete handle in each isolated control.

### Phase 2 - Prove version-slot execution and replacement

- [x] Publish immutable slot-local service launchers through the existing
  construction owner; reduce stable entrypoints to bounded selection/forwarding
  with all their file handles closed before long-lived execution.
- [x] Preserve arguments, environment/configuration, logging, `-ResolveOnly`,
  scheduling/start policy, native containment and service exit semantics.
  Do not detach a child whose task or cleanup owner cannot retain custody.
- [x] Compose existing incoming central-service retirement for an actual
  outgoing-process publication conflict. Bound the transition and recheck
  native identity, role, root and then-current selection before effects.
  Close self-held readers rather than trying to kill around them.
- [x] Preserve custom launchers, unrelated holders, session/worker children
  and current/later generations. Surface target path and truthful incomplete
  publication when the remaining OS conflict cannot be resolved.

### Phase 3 - Publish and reconcile the slice

- [ ] Refresh directly affected documentation and changefiles; pass focused
  unit/native and mandatory source checks, then normal PR review and merge.
- [ ] Observe actual Main release separately from Dev merge. Record final source
  acceptance and any unavailable lanes; do not claim installed/live acceptance.
- [ ] Resolve every Plan/Validation item or transfer it to a named tracked
  objective before Done; preserve the parent gate and deployment hold.

## Validation Plan

- [x] Failure-first controls distinguish a self-held header reader from a live
  outgoing wrapper's deny-delete handle on Windows PowerShell 5.1 and PowerShell 7.
- [x] Exercise the actual generated stable entrypoint and slot launcher in a
  disposable root: the service stays functional while stable bytes are replaced,
  invocation/configuration survive, and owned processes settle on exit.
- [x] Verify incoming setup retires only an identity-pinned outgoing central
  fixture when it actually blocks publication, and then publishes/selects the
  ready generation. Prove the conflict is resolved, not merely a pointer changed.
- [x] Refuse/skip termination for foreign-root, unknown-role, reused-PID,
  current/reselected/later generations and protected session/worker controls.
  Unrelated deny-delete holders produce honest bounded publication failure.
- [x] Prove fresh construction and old stable-launcher upgrade through real
  consumer boundaries, not only helper mocks; preserve database/configuration
  fixtures and scheduling policy.
- [x] Check headlessness and caller containment with exact owned cleanup.
  Do not weaken existing deadlines/assertions or waive #11960's native hold.
- [ ] Run focused tests, touched-Python Ruff, installation-contract and required
  CI/review. Record clean-room and live-venue applicability and omissions.

## Proposal

The proposal passed review and required CI in #12187, merged as
`a95f2e66609710b063e571da8ade9cd16fca8e36`. Exact-patch reconciliation permits
the separate launcher-body work without duplicating #11960's ownership-reader
correction. Its native qualification hold remains independent. No rollout is
claimed.

## Journal

### 2026-11-11 - Resume after tooling restoration

- Git is available again. Current #11960 remains open with an explicit native
  supervisor qualification hold; its contribution is not duplicated here.
- Claimed this distinct launcher-lifetime scope on #11875. The existing installer
  vision already requires authoritative incoming selection and bounded,
  central-only outgoing retirement; no vision extension is needed.

### 2026-11-11 - Proposal accepted; intersecting source retained

- #12187 received approval at exact head
  `d1261c9aab8ec32aab87e67e2ea29f79245a5ea2`, passed required CI and merged.
  The continuing source worktree was pulled forward onto the accepted plan.
- Direct source inspection confirms the stable generated service scripts contain
  the long-lived coordinator/supervisor invocation. The installer's first-line
  reader and a genuinely outgoing process are distinct handle-lifetime cases;
  neither the historical exception nor this trace identifies its actual holder.
- #11960 remains open at `1dd4b6f452f90c1d031604247b0ba1d3b8e449e0`,
  explicitly blocked by adjacent native supervisor qualification. Its reader fix
  and hold remain with its contributor. The generated launcher bodies and
  publication statements are an actual edit intersection, not merely a shared
  plugin name.
- Requested sequencing or a nonoverlapping edit agreement on that PR. The next
  source action is to reconcile its accepted correction or that agreement,
  then continue Phase 1 and implement the separately reviewed lifetime scope.
  No competing reader patch, native-test waiver, host repair or service
  termination was performed. Original parent acceptance and resources remain.

### 2026-11-11 - Exact-patch reconciliation

- The operator requested reconciliation against #11960. At exact head
  `1dd4b6f452f90c1d031604247b0ba1d3b8e449e0`, its runtime change is the
  first-line ownership reader, before generated launcher construction; it does
  not change those generated bodies. A shared file alone is not a dependency.
- Preserve that block and its new tests/changefile. Develop slot-local execution
  separately, check the contributor patch still applies, and keep unresolved
  native supervisor qualification explicit. Do not copy its patch into a
  competing PR or reinterpret the original failure's unknown holder as proven.

### 2026-11-11 - Slot-local execution and exact-holder qualification

- Extracted the generated service bodies into packaged immutable-slot resources.
  Stable launchers retain lookup and read-only resolution, then forward without
  detaching. Native forwarding controls prove the stable file is exclusively
  openable during slot execution on both PowerShell hosts.
- The activation owner captures the exact replaced generation. Sharing-conflict
  recovery holds the existing activation mutex and combines Restart Manager's
  actual file-holder birth with native inventory and admitted central-role,
  source-slot and ancestry proof. Selection and holder identity are refreshed
  before a handle-pinned termination; unresolved publication remains failure.
- A contained control exposed collateral child exit when an early candidate
  also terminated the non-holder central: its test-owned Job closed. Corrected
  the target set rather than weakening containment. Only the actual generated
  PowerShell holder is now terminated. The central, its Jobs and children are
  left running for their independent lifecycle. Raw paused-process custody is
  not introduced.
- The combined focused packet passed **36 tests**, including real changed-file
  publication for both service roles and PowerShell hosts, native central/child
  survival and unit foreign/current/later/reused-identity/changed-selection
  refusals. This is disposable source qualification, not host rollout.
- Direct marker-only rollback to a package predating slot launcher resources
  reports a named incompatibility; installing that older release recreates its
  matching stable entrypoint. `-ResolveOnly` remains available for old slots.
  Final packaging, expanded affected checks, required CI/review and release
  observation remain open. The reader contributor's independent hold remains.

### 2026-11-11 - Expanded qualification remains open

- The real wheel built successfully, and a fresh isolated installed-wheel
  consumer found both service resources. Install-contract, headless launch and
  touched-Python checks passed. The broader installer/scheduling packet had
  165 passing controls and one skip; two outdated source-shape assertions were
  corrected, then their complete two-file packet passed 52 tests.
- Tooling qualification exposed legacy tests comparing stale Development copies
  instead of the release materializer, plus linked-source checks that traversed
  an untrusted Windows link before rejecting it. Tests now exercise the existing
  candidate materializer, and the verifier refuses the link before resolution.
  The complete touched tooling packet passed 171 tests with two skips.
  Development layout validation passes; no shared payload copies were refreshed.
- Native executable-name qualification now covers bare scheduled-task executable
  names as well as fully qualified paths. The current retirement packet passed
  22 tests, including both native PowerShell hosts and central-role fixtures.
- The actual supervisor-admission consumer packet is not green: seven pass,
  while marked-master profile handoff reports an exited startup child after
  singleton refusal, and the repeated installer callback exceeds its unchanged
  two-cycle deadline and then fails bounded cleanup. No deadline, assertion,
  containment or independent contributor hold is relaxed. These are current
  qualification blockers, not declared flakes or source acceptance. Continue
  bounded diagnosis before publication or merge; no feature PR exists yet.

### 2026-11-11 - Native admission diagnosis

- A deterministic native-lock control proves that the master readiness observer
  can acquire a nonexistent singleton and refuse the real startup acquisition
  while writing its probe. Master observation now leaves a missing lock absent;
  existing-lock, native-identity and completed-cycle checks remain unchanged.
  The new regression and the isolated marked-master handoff pass.
- Repeated callbacks remain unqualified. Timing isolates cold startup and
  redundant native scheduling reads rather than an observed incorrect profile
  binding. A disposable consumer profile measured three policy subprocesses
  costing 3.2 seconds in one startup. Combine configured intent and primary
  task observation through the same policy owner, preserving its final
  admission-time recheck and independent disabled-profile behavior.
- Shortening or restoring fixture sleep intervals did not establish acceptance.
  Retain the original business fixture after diagnosis. Temporary tracing and
  profiling controls were removed; deadlines, assertions, caller Jobs and
  cleanup are not weakened. Focused policy/readiness/native qualification is
  in progress. This is source work only; independent holds remain.
- The combined policy packet passed 93 controls but did not clear the repeated
  callback's original budget. Subsequent cold-path qualification passed 113
  controls; the remaining failures exposed another real contract defect:
  cycle-start publication erased the observable completion proof, so even a
  successful ensure could immediately appear incomplete in its next cycle.
- Preserve successful completion as optional native-generation/version-bound
  evidence, independently of unchanged current-cycle progress. Clear it across
  generation/settings changes; malformed retained evidence cannot use the
  downlevel fallback. Native assertions now check that exact finite completion
  and its current generation rather than racing the next cycle's timestamps.
  Add portable retention/reset regressions and keep original native deadlines,
  teardown and current health semantics. No acceptance or publication is claimed
  while the focused retained-completion packet is running.
- Final focused qualification passed **100 tests**, including all nine actual
  supervisor-admission cases, the original repeated-callback deadline and
  strict native cleanup. The candidate's own bounded parser removes unrelated
  queue-parser startup while retaining its native admission handler and the
  normal global-authority/task-worker fallback. No diagnostic instrumentation
  remains. A separate affected-consumer packet passed **383 tests with one
  skip**, covering the full CLI, Source preparation, profile handoff and health.
- Custom-launcher refusal now requires the generated first-line marker; a
  copied marker later in custom content is not retirement authority. Its
  focused refusal controls pass. Owned wheel construction artifacts were
  removed after the already-passing packaged-resource consumer check.
  Final source checks, review/CI, merge and Main release observation remain.
  The reader contribution is still open at its original head with its own
  hold: qualification of these distinct source fixes does not waive it.
- Final install-contract, version consistency, documentation consistency,
  headless-launch and touched-Python checks pass. The contributor's exact
  installer/test/documentation patch still applies without importing it;
  its derived declaration must be regenerated when its owner composes it.
  Transition to Phase 3 for reviewed source publication, mandatory CI and
  actual Main release observation. No host update or other rollout is included.
- Dev integration intersects only the tooling materializer tests. Preserve its
  added retained-GUI-bootstrap control and this contribution's candidate
  materializer helper. Dev also supplies a shared Windows pytest-current-link
  prevention owner; reuse it from tool-test configuration and drop the earlier
  redundant cleanup helper/tests. Requalify that reconciled tooling path before
  publication. Dispatch source and its completed native qualification are
  unchanged by this integration.

### 2026-11-12 - Implementation review and real entrypoint coverage

- The first implementation review found two real defects: an early command
  selected the still-initializing live CLI module before its core helpers
  existed, and first-line holder qualification missed the supervisor's actual
  v1 marker. Require the live core helpers before selecting that module; otherwise
  use the initialized import. Accept the exact supervisor v1 first line while
  retaining legacy-header and custom-script refusal.
- Header retirement controls now obtain the first line from the actual
  supervisor writer, rather than a fabricated legacy header. Seven focused
  resolver/header controls pass across both PowerShell hosts.
- Add a real `python -m agent_dispatch` flow that starts a disposable coordinator
  and then admits a real supervisor. Business methods, client access and CLI
  helpers are not replaced; only host scheduling retains the existing absent-task
  adapter. It passes in 41 seconds with generation/completion checks and strict
  native cleanup. Its two startup operations retain their individual existing
  60-second bounds; the original nine cases' deadlines are unchanged.
- Dev has separately added closed-wheel installer inputs. Compose that
  independent change and regenerate the affected human-output declaration,
  rather than choosing either side's content hash. Refresh affected qualification,
  required CI and review on the resulting head before merge.
