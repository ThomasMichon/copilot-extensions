# Dispatch Reversible Action Controls

- **Slug:** `dispatch-reversible-action-controls`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed source slices targeting `dev`
- **Created:** 2026-10-27
- **Status:** Active
- **Vision:** [agent-dispatch](../../../visions/plugins/agent-dispatch/README.md#reversible-action-inhibition)
- **Umbrella issue:** #9334

## Guiding Intent

Keep the coordinator, supervisor and lane polling loops available while an
operator temporarily inhibits selected actions. Clearing an inhibition lets the
existing consumer resume the action on its next eligible poll or caller retry;
normal restoration must not require a manual process restart.

Do not conflate an action KillSwitch with service removal, static startup
configuration, a successful deployment or authority to discard existing work.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `placid-staple` | Source plan and bounded Dispatch implementation | Managed source PRs linked from #9334 |
| Repository reviewer | Independent plan and implementation review | Ordinary pull-request review |

## Coordination

- **Topology:** reviewed proposal, then dependent implementation PRs.
- **Host (owns PRs):** `placid-staple`.
- **Delegates:** bounded PowerShell implementation/testing under host integration;
  no independent publication, operational authority or workstream ownership.
- **Handoff:** issue, effort checklists and merged source receipts; preserve
  unresolved release and installed-consumer qualification explicitly.
- Existing #6820 preparation/sequencing and #7694 launch-maintenance owners
  remain authoritative. Do not build a competing updater or take their broader
  runtime-transaction work into this slice.

## Context

Current Dispatch self-update enablement is initialized independently for the
coordinator and supervisor. The shared bootstrap switch covers adopting
default-installation session-start reconciliation, not all resident actions.
Registration overrides remove a lane from the master's desired set, allowing
the master to stay up but winding down that lane.

The native update path also reconciles supervisor definitions after coordinator
cutover. Its graceful-only option prevents coordinator restart fallback; it
does not preserve the supervisor. Omitting supervisor installation removes its
units and is not an inhibition control.

**Reconciliation:** extend the Dispatch vision with live action inhibition,
while preserving existing override precedence and independent process
lifetimes. Follow `docs/patterns/service-lifecycle-supervision.md`,
`docs/patterns/service-contract-evolution.md` and `docs/install-contract.md`.
No new daemon, scheduler, task state or per-worker identity is required.

## Request

Public-safe statement of the requested generic behavior:

> KillSwitches restore functionality when cleared, ideally without a manual
> process restart. Polling consumers re-check each iteration; on-demand tools
> re-check when called again. Place inhibition only where retry or re-check is
> reliable. Do not prevent services from starting merely because an action is
> inhibited.

Implementation and controlled Dispatch adoption are requested. This public
source plan does not authorize an arbitrary installation change, foreign
worker termination or cross-plugin rollout.

## Plan

### Phase 1 - Review the action contract

- [x] Land this proposal through independent review before implementation.
- [x] Distinguish shared bootstrap state, installation-scoped action inhibition,
      static self-update configuration and existing lane-stop overrides.
      New controls must not silently reinterpret the latter two.
- [x] Define effective-state reporting through existing CLI/status/logging
      surfaces. Report the affected action, scope, reason and state source
      without exposing private task content or credentials.

### Phase 2 - Implement the recurring action boundaries

- [x] Reuse existing installation ownership and atomic operator-state mutation
      primitives, not a second control service. Keep namespaced installations
      independent of the legacy global bootstrap file.
- [x] Re-read effective state before coordinator and supervisor automatic
      replacement. Keep their polls alive while inhibited; clear must resume
      normal eligibility checks without a manual restart. Existing static
      configuration remains an independent veto.
- [x] Keep lane polling and existing-work settlement alive while new-job
      admission is inhibited. Guard the actual reservation/claim/launch paths
      used by supported consumers, not only a UI toggle or candidate listing.
      Document the exact scope rather than claiming a global fence from a
      local supervisor check.
- [x] Preserve original task/reservation/session identity and independent
      safety checks. Inhibition does not consume work, exhaust retry budgets,
      clear claims, authorize replay or classify an unknown effect as absent.

### Phase 3 - Qualify the maintenance boundaries

- [ ] Guard optional automatic runtime reconciliation and the explicit
      activation/coordinator/supervisor maintenance effects addressed by the
      control contract. Service startup from an available qualified runtime
      remains possible; missing prerequisites fail explicitly.
- [ ] Cover both installer-induced supervisor reconciliation and resident
      self-update. Skipping one while the other can replace the same supervisor
      does not establish preservation.
- [ ] Resolve required action policy before active-state publication; check
      current denial again at consequential boundaries. Freeze only one action
      attempt, not the daemon lifetime. Each later poll/retry is a fresh check.
- [ ] Keep explicit preparation separate from adoption using existing runtime
      ownership primitives; do not claim a new transactional rollback protocol.
      Coordinate any missing prepare-only primitive with #6820 rather than
      mutating active selectors as a staging shortcut.
- [ ] Implement equivalent POSIX and PowerShell maintenance behavior. An
      inhibited required effect is not a successful full update; retain exact
      partial-effect evidence on real downstream failures.

### Phase 4 - Release and qualify actual consumers

- [ ] Add changefiles, update the owning documentation and pass the relevant
      bounded tests, lint, install-contract and required repository gates.
- [ ] Merge reviewed implementation and verify the actual released `main`
      content, not ancestry or package-version assumptions alone.
- [ ] Under the installation owner's separate rollout authority, qualify
      installed commands and running consumers. An old process cannot acquire
      support merely because a new state file exists.
- [ ] Record running adoption or transfer that remaining gate to a named
      tracked objective before declaring this effort Done.

## Validation Plan

- [ ] Start each supported service with inhibition active: it reaches readiness,
      keeps status and polling available, and makes zero selected effect calls.
- [ ] Clear state without restarting the consumer and observe the next eligible
      poll admit the action. Job admission retains its consumer PID; automatic
      update may itself replace the process after admission.
- [ ] Exercise repeated on/clear cycles and a long-lived on-demand caller that
      retries. No process-lifetime denial cache or extra watcher is required.
- [ ] Assert actual effect-boundary calls are absent, not merely a reported
      disabled flag. Cover coordinator replacement, native supervisor
      reconciliation, resident successor launch and new-job admission.
- [ ] Preserve existing pending/active work and settlement while admission is
      inhibited. Unpause does not discard independent lane overrides, bypass
      exclusivity or produce a second worker for retained execution.
- [ ] Cover missing and malformed state, strict boolean validation, linked or
      foreign inputs, namespaced/custom-root isolation and explicit old-consumer
      refusal. Invalid explicit action policy must be visible while service
      availability and future re-check remain intact.
- [ ] Preserve existing default behavior and legacy bootstrap compatibility.
      Test default-installation fallback state paths and static configuration
      independently of dynamic inhibition.
- [ ] Exercise both native installer frontends for maintenance changes, with a
      contained process-boundary control where practical. Source fixtures do
      not establish running native-account adoption.

## Proposal

The checklists are proposed implementation means. The requested restoration and
service-availability behavior is normative; exact CLI spelling and state layout
must reuse the owning source conventions and be documented before publication.
Initial tests must prove the current gap before adding the guards.

## Journal

### 2026-10-27 - Proposal

- Claimed #9334 after checking related bootstrap, lifecycle and launch-maintenance
  work. The adjacent maintenance owners remain separate.
- Traced process-initialized self-update settings, the lane desired-set override
  and the update's unconditional supervisor-reconciliation call.
- Proposal only: no runtime implementation or installed control change yet.

### 2026-10-27 - Proposal reviewed

- #9337 merged as `e61e81219c` after the current-head Copilot review and required
  checks passed. Its sole Low finding requested the PR's documentation-impact
  assessment; that metadata was corrected and the thread resolved.
- Re-read the complete Plan and Validation Plan. Only proposal review is
  complete; action readers, consumers, maintenance controls and running
  qualification remain open.
- Begin with a behavioral regression against the actual supervisor decision:
  active shared state must leave it and its lane alive, then clearing state
  must let that same consumer admit automatic replacement.

### 2026-10-27 - Resident self-update implementation

- The initial two actual-supervisor regression cases failed at the unguarded
  replacement boundary before implementation, then passed with live checks.
- Added a dependency-free bounded policy reader, an installation-aware runtime
  adapter, and local `actions contract|status|inhibit|clear` commands. They reuse
  the existing serialized atomic mutation primitive but never reinterpret the
  lane-stop override file.
- Current command support advertises only the two actual resident consumers:
  coordinator replacement and supervisor self-update. Native maintenance and
  worker-admission actions must be added by subsequent reviewed slices, not
  advertised prematurely. The [owning guide](../../../plugins/agent-dispatch/docs/action-controls.md)
  documents this limit and running-consumer qualification.
- The actual coordinator lifespan loop remains ready through inhibited polls
  and admits replacement after state is cleared; supervisor tests also cover
  retaining its lane and lease. Static startup settings remain independent.
- Source implementation and contained tests are not release, installed adoption
  or completion of this effort. Next: review this bounded resident slice, then
  implement the remaining admission and native maintenance boundaries before
  controlled running qualification.

### 2026-10-27 - Resident review corrections

- #9345's current-head independent review identified three portability gaps:
  shared-directory probing could escape the reader's error boundary, Windows
  newline translation could invalidate a near-limit policy after publication,
  and synthetic home isolation omitted Windows `USERPROFILE`.
- Moved primary/fallback selection inside the bounded reader's shared-state
  error handling. Permission errors remain visible legacy fail-open decisions,
  and later checks observe restored access rather than a dead polling loop.
- The existing atomic writer now emits UTF-8/LF on every platform, matching the
  pre-publication byte limit. A maximum-size policy remains readable/clearable.
  Added regressions for both fixes and set both home variables in shared-state
  consumer tests. These portable contained cases do not assert native installed
  Windows adoption.

### 2026-10-27 - Resident slice merged

- #9345 merged as `25303a9926` after current-head independent review, correction
  of all Medium findings and required checks. Re-running its exact attribution
  job validated the repaired description without changing the source head.
- Only the resident-control mechanism items above are complete. Admission,
  native maintenance, release and actual consumer qualification remain open.
- Next: guard coordinator claim/reservation mutations and supervisor new-launch
  attempts while leaving existing-work settlement and polling available.

### 2026-10-27 - Worker admission

- Five actual coordinator/lane/redrive regressions failed at their effect
  boundaries before implementation. The coordinator now injects an explicit
  queue admission callback; pure library embeddings remain policy-neutral.
- Claims recheck before mutation, new reservations check before consuming an
  attempt, and redrives check before their startup fence. Existing-work
  settlement remains available. A single already-admitted launch can finish;
  later attempts recheck rather than caching denial for the daemon lifetime.
- The [owning guide](../../../plugins/agent-dispatch/docs/action-controls.md)
  records supported scopes, explicit REST/MCP denial, retry behavior and skew
  limits. Final bounded selection: 112 cases passed, including actual HTTP MCP
  retry against the same coordinator, pending identity and independent holds.
- Native maintenance, release and installed-consumer qualification remain open.
- Followed the same launch trace through restricted startup reconciliation and
  optional one-shot handoff fallback. Two further regressions proved both
  bypasses before adding guards ahead of preparation/attempt consumption.

### 2026-10-27 - Admission review corrections

- #9351's independent review found atomic create-and-claim bypassing the
  callback, and a launched script treating startup denial as terminal failure.
  Actual REST/script regressions reproduced both gaps.
- Guarded atomic create-and-claim after deduplication and before mutation, with
  a second check before claim publication. A denial rolls back task/audit and
  supersession writes rather than leaving a partially accepted request.
- Added a structured retryable REST denial and same-worker script startup retry.
  Cancellation preserves the unclaimed task. Gone unclaimed scripts retain
  their reservation while inhibited, then use the existing failed-spawn retry
  after clear; claimed scripts keep their prior failure semantics.
- A second review found the pending flag cleared before validating a returned
  claim. An independent hold applied during inhibition could therefore trigger
  terminal abandonment after clear, although the script never owned the task.
  Keep the pending flag until the claim is a mapping for this exact task.
  Regressions cover a real coordinator hold/clear transition and all invalid
  claim shapes without start or abandonment.
- Extended the pending-cancellation guard to normal context exit after the
  script handles cancellation internally. No terminal report is required or
  sent while admission is still pending, whether cancellation escapes or is
  caught by the body; claimed script terminal reporting remains unchanged.

### 2026-10-27 - Admission slice merged

- #9351 merged as `20ec6d6544` after exact-head independent review, correction of
  all High/Medium findings and all three required checks. The final targeted
  startup/script lifecycle selection passed 21 cases, including independent
  holds and both escaped and handled pending cancellation.
- Only the admission mechanism items in Phase 2 are closed by this slice.
  Native maintenance, release and actual consumer qualification remain open.
- Next: implement the independent native action checks while preserving
  #6820's preparation/retention ownership. The requested prepare-only interface
  has not been supplied or accepted; do not activate a runtime as staging.

### 2026-10-27 - Native maintenance implementation

- Four actual POSIX installer regressions demonstrated forbidden runtime/removal
  effects before guards. The native decision helper now uses the same bounded
  reader without a runtime venv, and full maintenance preflights its required
  effects before instance reconciliation or active publication.
- Added native coordinator/supervisor guards, explicit legacy/conditional
  cutover admission, cold first-use and session-start reconciliation checks.
  Available qualified runtime execution remains ahead of cold update work.
  Exit-3 cutover denial cannot become a restart fallback within that attempt.
- Retained the existing Python-free explicit stamp only after proving ordinary
  policy absent through unlinked, accessible ancestry. Present or unprovable
  state is not permission. Kept the shared layout library policy-neutral.
- Focused native/policy/activation/cutover selection passed 76 cases; the
  source-publication selection passed 59 with 17 platform skips. Actual
  contained WSL PowerShell execution additionally passed all 29 new admission
  cases; parsing or skipped devcontainer cases were not substituted for that
  behavioral evidence. This does not qualify Windows production adoption.
- Existing extracted PowerShell fixtures now load the real admission
  dependency. Native execution exposed a missing `Write-Step` fixture stub;
  that small harness defect was corrected and all eight stamp cases passed.
- Independent review, release and installed-consumer qualification remain
  pending. The #6820 preparation/retention interface is still unanswered; this
  slice does not introduce preparation or claim supervisor-preserving adoption.

### 2026-10-27 - Native refusal propagation review

- #9359's independent review found both native frontends losing the distinct
  refusal status at installer/process boundaries. Clearing policy before an
  automatic fallback could then admit maintenance within an already-denied
  attempt; a fixture that directly exited 3 did not prove the real boundary.
- Three actual POSIX regressions reproduced status 3 becoming 1 in installer
  callers and publication recovery. Preserve the original status through
  cleanup and callers, and retain refusal when a later native step fails.
- PowerShell guards retain terminating-error semantics with structured refusal
  metadata. The real installer maps only that refusal to exit 3 after cleanup,
  including restore-parent preflight, while unrelated failures retain their
  original classification. Background workers preserve native exit 3 even when
  PowerShell's invocation-success flag is false.
- Added real-installer and late-publication cases, rather than relying only on
  a fixture's hardcoded status. No installed adoption or preparation interface
  is established by these source corrections.

### 2026-10-27 - Native file-failure review

- The next #9359 review found checked Bash callers suppressing `errexit` inside
  their native helper bodies. Required directory, environment, launcher, unit
  and removal failures could therefore reach later service effects and return
  success. Thirteen actual failure cases reproduced status 0 before correction;
  two ordinary-effect controls confirmed the fixture did not merely block all
  maintenance.
- Explicitly propagate required filesystem failures through native helpers,
  profile/removal loops and their callers. Preserve refusal status and release
  the cutover lock, while leaving existing audited best-effort native-manager
  and diagnostic behavior unchanged.
- Fixed the full missing-guard fixture class in required-cutover and disabled
  master/profile tests, using the real guard and contained installation roots.
  Their original cutover/hold assertions remain intact.
- The combined contained selection passed 59 cases with 24 PowerShell skips;
  the affected host-native harnesses actually ran and passed all 65 cases.
  The expanded filesystem matrix separately passed all 20 cases, including
  nested profiles/removal and the no-restart update branch.
- Re-review and required checks remain separate. Preparation, release and
  installed adoption are still open; this is not operational recovery.

### 2026-10-28 - Lock-backend test portability

- The next review found the cleanup assertion unconditionally probing `flock`,
  although native update selects a PID-symlink lock when `flock` is missing or
  explicitly disabled.
- Reused one backend-matched assertion in both affected test contracts. The
  forced PID case supplies a failing `flock` executable and proves it is never
  called; a dangling unremoved PID link is an explicit negative control.
- The contained selection passed 60 cases with 24 PowerShell skips, covering
  both selected lock paths. Runtime code and the previously executed native
  PowerShell cases are unchanged.

### 2026-10-28 - Lexical policy-root admission

- The next review reported an unpublished linked-root bypass despite its
  contradictory no-open-issues header. Actual POSIX cases reproduced trailing
  separators hiding a symbolic root from the absence fast path.
- Checked the same class in PowerShell: normalizing parent traversal erased
  an intermediate symbolic component before absence was established. That
  separate case also reproduced the incorrect allowance.
- Strip only trailing separators from the POSIX inspection path, and walk
  PowerShell parents lexically rather than resolving their full names. Linked
  roots remain refused without changing the requested installation.
- The three actual failing cases now pass alongside ordinary-root controls:
  the contained selection passed 61 cases and actual host PowerShell passed all
  54 cases. Ordinary roots with the same spellings remain Python-free; linked
  roots still refuse without changing policy or active state.
- Re-review, release, the accepted preparation interface and real running
  qualification remain separate gates.

### 2026-10-28 - Captured cutover policy scope

- Assessed the next review's cross-installation warning against actual cutover
  paths. Four regressions reproduced service-environment root/context redirects,
  lexical alias normalization and a conditional target checked against the
  caller's installation.
- Legacy cutover validates its captured environment snapshot before publishing
  any overlay. It refuses root/context redirects without changing caller state,
  while retaining ordinary same-root, host and port pins.
- Conditional callers now supply the target's captured policy root explicitly.
  Missing context refuses before reading ambient installation state. Preserve
  the existing conditional contract: no caller configuration or service overlay,
  no legacy prelude, and no inferred relationship between caller routing and an
  authoritative captured target snapshot.
- The contained policy, conditional-deploy and deploy-CLI selection passed all
  32 cases, including unrelated caller configuration and custom target routing.
  Re-review and required checks remain pending; preparation, release and actual
  adoption are still separate.

### 2026-10-28 - Windows environment-key review

- The next independent review found mixed-case service-environment keys
  bypassing the regular dictionary's root/context validation before Windows'
  case-insensitive environment applied the redirect. Four contained cases
  reproduced the bypass or lost ordinary pins.
- Canonicalize keys in the shared overlay under Windows semantics, before
  cutover validation or publication. This also keeps first-use replacement
  consumers consistent. Preserve POSIX case sensitivity and native last-pin
  precedence; do not normalize installation paths to hide linked components.
- Extend the actual cutover cases for mixed-case root/context/alias refusal
  and ordinary pins, plus direct shared-overlay overwrite controls.
  Source regressions emulate environment-key semantics; they are not native
  Windows installation or running-adoption evidence.
- The expanded selection passed all 49 cases. Existing launch fixtures now
  capture the shared daemon boundary instead of asking the real instance
  registry to commit a fictitious PID. Actual Dispatch environment, fresh-port,
  host, working-directory and resident-launch option assertions remain; shared
  native launch policy stays with its owning tests.

### 2026-10-28 - Routing-target review assessment

- The next independent review requested assessment of an unpublished critical
  routing-policy bypass. Four actual cases reproduced a service overlay
  replacing or clearing the routing directory after installation admission,
  for canonical and Windows-style mixed-case keys.
- Capture legacy routing before reading the service overlay, refuse retargeting
  without mutating caller state, and retain that one target through lock,
  recovery, publication and replacement environment. Explicit caller-selected
  custom routing and identical durable route pins remain supported; conditional
  deployment still uses its separately captured target without caller preludes.
- The policy, conditional, deploy, overlay and launch-contract selection passed
  all 55 cases, including a replacement spawn retaining its routing snapshot
  after caller environment changes.
- The native-fixture assessment reproduced two bootstrap cases exiting 126
  because the copied payload omitted the real policy reader. Stage the actual
  reader and source dependencies in POSIX/Windows lifecycle fixtures; the
  PowerShell control now proves denial and admission after clear through that
  copied payload, including a quoted payload path.
- Four Windows-string regressions exposed Git Bash separator/case handling and
  launcher aliases in the test helper. Normalize those representations while
  continuing to refuse WSL-launcher aliases as native Bash.
- Independent integration passed 66 contained routing/bootstrap cases and 55
  actual host PowerShell cases. Nine native-Windows lifecycle cases remain
  explicitly skipped on WSL; neither their staging nor the host PowerShell
  execution establishes native Windows adoption.

### 2026-10-28 - Cutover test-environment isolation

- Assessed the next review's teardown concern: removing an absent environment
  key through `monkeypatch` does not register a later direct production overlay
  write for undo. Ordinary host/port/routing pins could outlive a test.
- Extract the existing copied-environment fixture and apply it to all four
  modules that directly execute cutover. Each case gets its own overlay target
  and asserts the parent mapping is unchanged at teardown; other suites retain
  their existing environment semantics.
- The integrated routing/bootstrap selection again passed all 66 cases with
  the teardown assertions active. Runtime/native code and the previously
  executed actual PowerShell portfolio are unchanged.

### 2026-10-28 - Detached environment expansion

- The next assessment identified queue selection resolving references against
  the triggering process instead of the detached overlay. Five actual
  dollar/braced/Windows-percent cases reproduced a wrong or unexpanded database
  path while a POSIX percent-literal control remained correct.
- Expand references from the supplied snapshot, including earlier durable
  pins, without a temporary process-global environment mutation. Preserve
  native case/quote/escape/unknown-variable behavior with direct POSIX/Windows
  standard-library syntax controls; the shared helper serves first-use and
  cutover consumers consistently.
- The integrated selection passed 105 cases, including actual cutover
  configuration and replacement environment selecting the same durable queue
  despite unrelated caller state.
- The installer-fixture assessment reproduced an unmatched PowerShell closing
  brace after the shipped outer-try refactor and omitted real guard dependencies
  in lifecycle/retained-runtime function extraction. Reconstruct the actual
  try/catch/finally boundary, stage real guards, isolate unrelated reconciliation
  effects and keep recorder-I/O failures separate from valid policy roots.
  Original lifecycle and retained-runtime assertions remain unchanged.
- Independent host execution passed all 75 affected lifecycle/required-cutover
  cases and all 15 retained-runtime cases, including actual WSL PowerShell.
  This does not qualify native Windows deployment or running adoption.

### 2026-10-28 - Unicode suffix expansion parity

- Independent review found unbraced POSIX variable matching consuming Unicode
  suffix characters, unlike the standard library's ASCII variable-name rule.
  Two actual oracle cases reproduced the wrong durable path; braced references
  and Windows syntax remained correct.
- Compile the POSIX pattern with `re.ASCII` and retain non-ASCII path suffixes
  outside the reference. Extend the native-syntax controls for two Unicode
  suffix classes and a braced control without changing Windows matching.
- The integrated contained selection passed all 111 cases. The native
  installer and previously executed PowerShell lifecycle portfolio are unchanged.

### 2026-10-28 - Unmatched Windows reference boundaries

- Extended the native-parity assessment to compound reference syntax rather
  than checking only isolated tokens. The deterministic oracle reproduced a
  Windows unmatched percent reference incorrectly allowing later dollar
  expansion; the concurrent review independently requested this assessment.
- Preserve the remaining literal tail after an unmatched Windows percent or
  braced-dollar opener, matching `ntpath.expandvars`. POSIX keeps its distinct
  native behavior.
- Explicit unmatched-token controls and 1,000 deterministic compound comparisons
  per backend now pass. The integrated contained selection passed all 121 tests;
  native installer/lifecycle code is unchanged.

### 2026-10-28 - Native Windows qualification gate

- The next independent review requested actual Windows installer cleanup,
  refusal propagation and background-worker boundaries, rather than counting
  WSL PowerShell as that platform's evidence.
- Extend the existing manual installation portfolio with three focused
  Dispatch maintenance packets on native Windows. Pin and verify the dispatch
  SHA, retain finite runner/test/job limits and disposable roots, and leave the
  required unit-only PR gate and unavailable full Windows rotation unchanged.
- Native execution and actual test outcomes remain pending; declaring the
  workflow or copying fixtures does not establish qualification or adoption.
- The first immutable native Windows run at `137b6b0403` passed the policy packet
  (147 tests, two explicit skips) and the worker packet (20 tests, including all
  nine previously Windows-only lifecycle cases). The installer packet exposed
  11 Git Bash harness failures before original assertions: the real guard needed
  bootstrap-Python/error-helper prerequisites absent from extracted fixtures.
  Its 63 passes and 16 skips do not make that packet successful.
- Report skip reasons in all three packets. The remaining fixture corrections
  and a fresh exact-head native run are required before qualification.
- The affected SH fixtures now supply the real reader/interpreter and error
  prerequisites separately from the fake cutover/health responder, using native
  Git Bash-compatible paths. Valid explicit policy forces the reader path on
  Linux too; a real denial/clear control preserves maintenance checks rather
  than bypassing them. Forced-reader execution first reproduced nine local
  failures; independent full affected-file validation now passes all 64 cases.
  The fresh native Windows run remains pending.
- The second native run again passed policy (147 tests; its two skips are
  explicitly POSIX unprivileged access-denial controls) and worker lifecycle
  (20 tests). Installer execution reached the previously masked SH lock path,
  then timed out. The existing activation harness already documents Git Bash's
  required native-symlink mode for numeric PID-link targets; extract and reuse
  that environment in the required-cutover harness rather than changing the
  production lock, increasing its timeout or skipping the assertions.
  The affected shared/required/retained local selection passes all 78 cases.
  Exact-new-head native qualification is still required.
- The third native installer packet completed the PID-link path and all update
  and retained-runtime cases; only two SH boolean-success controls still failed
  (73 passed, 16 explicit POSIX-only skips). Their Windows fixture selected a
  CMD responder solely from the host OS, even when called by Bash, losing the
  inline Python argument boundary. Select the responder by caller shell, use
  literal LF for shell scripts and retain the original real JSON predicate and
  all positive/negative assertions. The affected local selection passes 64 cases;
  final native execution remains pending.

### 2026-10-28 - Bootstrap root spelling and late-refusal diagnostics

- The next assessment identified canonicalization hiding linked ancestry before
  automatic bootstrap admission, and late refusals leaving missing or stale
  diagnostics. Four actual POSIX cases and four actual PowerShell cases
  reproduced the source gaps; ordinary-root, namespace and warm-runtime controls
  remained available.
- Preserve the selected lexical root alongside governance's canonical effect
  root for cold legacy checks; do not normalize it before the reader or consult
  an unselected legacy root in namespace mode. Relative/Windows drive-relative
  and root-relative inputs fail explicitly using compatible native APIs.
- Settle owned POSIX provisioning status on late exit while its lock is held.
  Persist attempt-tagged PowerShell worker refusal diagnostics inside the
  existing status fence, including missing/stale logs, failed log writes and
  superseded-attempt controls. Retain refusal classification on diagnostic
  failures; no installer fallback, extra attempt or service stop is introduced.
- Current local correction evidence and fresh native Windows qualification
  remain separate gates; the earlier `2d32e03562` native run is a baseline,
  not validation of these new frontend changes.
- Independent host PowerShell passed 75 cases with nine native-Windows
  lifecycle cases and two Windows-root-relative controls explicitly skipped
  on WSL. Contained frontend/source-publication compatibility passed 86 cases
  with 19 platform skips and exposed one stale exact-manifest expectation;
  include the already-declared `installerLifecycle: true` field, then that
  targeted declaration control passes too. No manifest behavior was changed.

### 2026-10-28 - Global default entrypoints and owned cold termination

- The next assessment identified global entrypoints resolving away the original
  home spelling, so the shared default bootstrap veto was not observed. Actual
  full-script cases reproduced the bypass through both aliased and physical
  entrypoints with a linked home; ordinary veto, warm execution and clear
  controls remained available.
- Reuse governance's existing canonical-home/default-root decision to retain
  the original platform-home spelling only for that GLOBAL legacy default.
  Keep canonical effect arguments, ignored ambient install overrides, custom
  and namespace isolation, and qualified warm execution unchanged. No shared
  installation/service identity classifier or new root resolver is introduced.
- PowerShell cold callers now settle their own successfully published attempt
  in `finally` while holding the provision lock, retaining refusal classification
  on status-write failure. This does not repair other/historical attempts or
  claim SIGKILL finalization. POSIX owned SIGTERM status settlement is exercised
  through an actual controlled installer process.
- Reconciled the real base conflict with the current `dev`: preserve both
  independent qualification contracts and all 17 source commits in order.
  Dispatch script bytes were unchanged by that rebase; the intervening shared
  source-build cache change is covered by the installed-source portfolio.
- Independent contained global/source-publication compatibility passed 92 cases
  with 19 platform skips; actual host PowerShell passed 84 with 11 explicitly
  native-Windows-only skips on WSL. Fresh exact-head native qualification and
  independent source review remain required.

### 2026-10-28 - Relative bootstrap interpreter selection

- Independent review identified a relative `PATH` interpreter working during
  initial hook admission but disappearing after the hook changed to home.
  The actual drift fixture reproduced exit 126 before reconciliation.
- Anchor the selected path to the original working directory before that
  change, preserving existing absolute forms and the selected interpreter leaf.
  Reuse it for both the fresh admission check and worker; no later PATH lookup
  or interpreter substitution is introduced.
- A portable real-interpreter fixture records absolute calls from the original
  directory and at least two from home, then verifies the worker's installer
  effect. The combined contained hook/native selection passed all 57 cases.
  Fresh exact-head native qualification and review remain required.
