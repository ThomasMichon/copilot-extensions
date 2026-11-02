# Picker Launch Handoff - Mini-effort

- **Parent:** [Lean Session Lifecycle](README.md), Phase 3
- **Tracking:** #5579; no duplicate implementation owner or umbrella issue
- **Created:** 2026-11-01
- **Status:** Active; query admission implemented and source-qualified, publication pending
- **Vision:** `visions/plugin-services/README.md` - transient callbacks and
  removal of session-scaled wait-only forwarding processes

## Guiding Intent

After an ordinary Windows Picker launch or resume reaches its mux attachment,
neither the Worktrees nor Manager Python forwarding layer should remain alive
just to wait. A terminal-owned PowerShell launch host is acceptable; direct
terminal-to-PSMux attachment is the preferred simplification if qualified.
This is the bounded launch slice of the parent effort, not a second campaign.

## Request

The operator's 2026-11-01 request, verbatim:

> So, we expect the Python part of the Manager launch to be gone; ideally only the mux-pane launched process should be live in the terminal window. Let's figure out what changed here. After Picker launches or resumes a worktree, I expect Picker's python process to be *gone*. We should see:
> wt.exe -> launch_session.ps1 -> psmux.exe -> mux_session.ps1 -> copilot.exe
> I'd love it if we could achieve
> wt.exe -> psmux.exe -> mux_session.ps1 -> copilot.exe
> (mux_session is a placeholder; I forget what the actual process is named)

The subsequent planning request, verbatim:

> Great. Make a mini-effort to tackle this.

After the original plan merged in #9913, the operator authorized source work:

> Great. Drive implementing these fixes.

## Scope Approvals

The final request authorizes source implementation and isolated qualification
of this reviewed mini-effort. The earlier request authorized the focused plan.
Neither authorizes
terminating retained launchers, migrating live sessions, updating installed
runtimes, changing Job policy, or lifting a deployment hold. Implementation
approval is not rollout authority. The #5579 driver owns this bounded Phase 3
continuation; the #8669 installation-independent-session owner is unchanged.

On 2026-11-02, the operator selected the admission choice, verbatim:

> probe

The selected form label was "Use a bounded selected-runtime capability query
(recommended)". This chooses the read-only selected-runtime query direction
below, including its transient startup work. It does not approve the alternative
constructor-metadata implementation, installed rollout, live-session changes
or a deployment-hold change. The admission amendment cleared source review in
#9943 before further protocol implementation.

## Context

- `agent_worktrees.manager_launch_cli._exec_worktree_manager` waits on
  Manager on Windows; the original forwarding seam dates to #484.
- `worktree_manager.relocated_launch._run_relocated_mux_launch` waits on
  `pwsh -File launch-session.ps1`. This behavior entered the relocated launch
  path in #2429 and was extracted without changing that wait in #5232.
- The outer Windows launcher now owns kill-on-close Job custody. Simply
  dropping its wait or exiting Python can kill the attachment descendants;
  it is not a valid handoff.
- `launch-session.ps1` attaches to PSMux, then performs return-path checks.
  The pane side is `pane-launch.ps1` invoking `pane-wrapper.ps1`, not a script
  literally named `mux_session.ps1`. An attachment client and its session
  server are separate process branches, so the requested chains express
  lifetime intent rather than a required literal ancestry.
- The resident Picker fast path also retains forwarding waits. Cover cold,
  resident and provider-session entry, not only the ordinary Manager entry.
- #8669 owns copied pane shells, current-installation lifecycle handlers and
  uninstall/replacement survival. Reuse that contract without taking over its
  seed-execution work. #5579 also retains broader command-shim and measured
  launcher-depth acceptance; this mini-effort does not close it by itself.

## Proposal

**Agent-recommended implementation direction:** make the terminal's shell host
own a two-phase handoff. Python produces the prepared selection/launch result
and exits; the shell consumes that result and performs launch/attachment using
the existing owner. Transfer cleanup custody explicitly before retiring the
Python forwarding owner. Do not introduce a detached unowned attachment, another
resident helper, arbitrary shell evaluation, or a Task/service/UAC fallback.

Start with the shell-hosted target. Evaluate direct PSMux attachment only after
the required path passes: the optimization must preserve attach-return cleanup,
terminal restoration and exit reporting, not hide those duties in another
wait-only process. Keep POSIX process replacement and standalone behavior.

### Selected-runtime admission amendment

A response-only environment offer is not safe for an unsupported runtime:
that peer can ignore the channel yet pass it into a daemon or detached pane,
whose later commands inherit a deleted request. Filtering only new resident
helpers does not fix an older peer. Do not reinterpret an invalid active
request as permission to launch, or retain abandoned channel files to hide
the problem.

**Operator-selected direction; implementation details remain reviewable:**
establish positive support through a bounded read-only query against the actual
selected execution runtime before offering the channel. Independently selected
receivers in the forwarding chain also require support before receiving it.
Do not infer support from a newer payload, a package version string alone or
an unqualified capability claim.

Use an effect-free query for both current and supported legacy peers, never a
flag appended after arbitrary operator arguments or `--`. Reuse the existing
finite-process owner to bound output and lifetime and confirm descendant
cleanup before admitting preparation. No implicit installation, network
bootstrap, unowned timed-out child, new daemon, Task or UAC fallback is allowed.
Explicitly unsupported peers retain legacy execution without the channel;
malformed/unavailable queries and uncertain cleanup remain visible and do not
authorize handoff or automatic replay.

The accepted channel belongs only to the foreground preparation flow. Capture
and forward it deliberately rather than leaving it in the environment of
resident services, reusable workers, launched panes or non-handoff children.
Preserve native failure results, literal empty arguments and the original
caller state. Measure and report the query's real cold/warm startup work,
including actual process counts; do not carry forward a claim of zero
discovery work.

Constructor-owned capability metadata was the unselected alternative. Existing
runtime-selection facts may locate the query target, but cannot stand in for
the actual capability response. The implementation remains drafted until the
reviewed admission revision is implemented and qualified.

## Plan

### Slice 1 - Agree the finite handoff

- [x] Resolve and review the admission amendment in #9943 before further protocol
  implementation; preserve the existing no-rollout boundary.
- [x] Confirm the current cold/resident/provider-session and project-binstub
  consumers; agree the boundary with the existing #5579 and #8669 owners.
- [x] Specify the smallest structured result and shell consumption contract:
  selected installation/runtime, literal argv/environment, cwd, cancellation,
  seed/attempt/receipt identity, exit status and cleanup custody.
- [x] Review the plan before runtime edits; identify the terminal/profile and
  same-window/new-window behavior without introducing an implicit preference.

### Slice 2 - Remove the two Python forwarding roles

- [x] Implement selected-runtime support discovery and foreground-only channel
  propagation, preserving unsupported-peer legacy behavior.
- [x] Implement the shell-owned preparation-to-attachment handoff for ordinary
  local Windows creation and resume, including existing-session joins.
- [x] Retain terminal input/output, Ctrl+C, post-exit versus detach behavior,
  installation-independent pane shells, exact process ownership and reaping.
- [ ] Add focused lifecycle regressions, update launch documentation and
  required changefiles, and land through the normal source-review gate.
- [x] Record whether direct terminal-to-PSMux attachment is qualified; if not,
  keep the shell-hosted result and record the precise remaining constraint.

### Slice 3 - Qualify the persistent shape

- [x] Prove the native process-lifetime contract in an isolated Windows terminal
  with one and several sessions; finish all test-owned resources explicitly.
- [ ] Record release availability separately from a separately authorized
  installed rollout and running-state proof; do not migrate existing sessions.
- [ ] Journal the bounded outcome in the parent and retain its unrelated open
  Plan/Validation items and #5579's remaining launcher-depth acceptance.

## Validation Plan

- [x] Actual selected-runtime support, not latest-payload metadata, gates the
  channel. Both mixed-version directions preserve legacy operation without
  leaking a request into later sessions.
- [x] Queries are effect-free for current/legacy peers, bounded and owned;
  missing/malformed/unavailable results, timeout and failed cleanup cannot
  admit handoff or silently trigger installation, retry or a second session.
- [x] No channel reaches resident or pane children. Repeated/concurrent later
  launches cannot reuse an expired offer after foreground completion.
- [ ] Cold/warm capability-query measurements classify every transient process
  and retain the zero persistent-forwarder requirement after attachment.
- [x] After creation and resume/attach are ready, actual process observations
  show zero persistent Python workers or interpreter trampolines serving the
  two forwarding roles; transient preparation/helpers are classified separately.
- [x] Native console/PTY tests prove stdin, rendering, resize, Ctrl+C, detach,
  rejoin, child exit and terminal close; no session loss or unowned child tree.
- [x] Literal argument tests cover spaces, quotes, Unicode, empty values,
  metacharacters and `--`; preserve selected runtime, cwd and profile behavior.
- [x] Cancellation, failed launch, missing/malformed/reused handoff, seed and
  receipt failure cannot report success or launch a second session.
- [x] Existing post-exit, launch-attempt, native-incarnation and Job/reaping
  tests pass; no-mux, remote, new-window and POSIX behavior is preserved.
- [x] Fresh constructed-runtime and repeated/concurrent launch tests exercise
  real binstubs and shell boundaries, not only mocked Python composition.
- [ ] The shared Manager mux daemon remains a singleton after bounded cutover;
  per-session PSMux servers and actual Copilot processes remain permitted.
- [ ] Source tests, native evidence, promotion and authorized installed proof
  remain distinct; skipped lanes have explicit reasons and tracked follow-up.

## Journal

### 2026-11-01 - Focused proposal

- Reused #5579 and the parent Phase 3 instead of opening a competing campaign.
- Traced both Windows wait sites and their history; this is a longstanding
  launch-lifetime gap, not evidence of duplicate Manager mux daemons.
- Captured the operator's required Python lifetime and preferred shorter chain.
  No runtime edits, live-session changes or deployment were performed.

### 2026-11-01 - Source execution admitted

- The proposal merged in #9913 after a clean approval and required CI.
  The operator then explicitly requested implementation.
- Implementing the outer PowerShell return channel and finite Manager producer.
  Python-owned preparation Jobs settle before the shell invokes the ordinary
  launcher; existing pane scripts and their custody remain unchanged.
- Initial generated-launcher controls pass in the isolated native ARM Python
  environment. The earlier x64 test environment was retained after Windows
  Restart Manager identified its denied DLL's holder as the OS emulation cache;
  no service was stopped or reconfigured. Full native handoff acceptance remains
  open and is not inferred from those compatibility controls.

### 2026-11-01 - Native source boundary qualified

- The finite channel is shared through both forwarding layers. A capable
  Manager publishes version-1 JSON; Python and its cleanup Jobs settle before
  the terminal's PowerShell host consumes it. The host deletes the channel
  before attachment, preserves literal argv, restores scoped environment/cwd
  and records the actual return through a finite callback.
- Native ConPTY controls exercise the actual Worktrees forwarder and copied
  user/attributable entry scripts, generated retained Manager CMD/PowerShell
  entries and a standalone Manager entry creating its own channel. Native
  handles pin every preparation descendant, including interpreter trampolines;
  none remains at the selected attachment boundary.
- A separate real PSMux control runs two simultaneous fixture sessions, delivers
  input and Ctrl+C, resizes, detaches/rejoins the same live pane and independently
  exits each child. Fixture Jobs, retained handles, isolated data roots and
  named-session cleanup own all test resources. The production launcher's
  refusal path additionally preserves exit 3, its journal observation and the
  original diagnostics without running preparation effects.
- Engine discovery, runtime-selection responses and the successful selected
  command are synthetic. These tests are not a full real-engine/Copilot launch,
  live installed inventory or a Manager-daemon singleton claim. Those live
  measurements remain with parent Phase 4 / #5579; copied-shell and lifecycle
  behavior owned by #8669 is not taken over here.
- Focused results: 134 launch/receipt/compatibility unit cases; 98 resident,
  outcome and earlier native cases; 20 provider-session cases; 10 entry/terminal
  cases plus the production-refusal case; one real concurrent mux lifecycle
  case; two existing real-pane producer cases; and the earlier three generated
  installed-slot and three selected-payload controls passed. These packets
  overlap and are not an additive coverage count.
- Existing post-exit/reaping controls pass (three cases), as do native-hosting
  identity controls (26 cases; two exact-held-file cases require Linux procfs
  and are explicitly skipped on Windows) and four launcher packaging/UTF-8
  controls. The 13-plugin installation-contract check remains clean.
- Native checks exposed and fixed nested-array PowerShell splats and a
  malformed non-object request. Existing receipt fixtures were updated to
  answer hosting admission before asserting the actual resolver behavior.
  Independent pre-submit review found no high-confidence runtime defect.
- Direct terminal-to-PSMux replacement is not qualified: the existing launcher
  still owns attach-return, detach/exit and post-exit bookkeeping. The reviewed
  shell-hosted target is retained rather than deleting those duties.
- No installed runtime was updated, no live session was migrated or killed,
  and no deployment hold was lifted. Source publication, promotion and a
  separately authorized installed rollout remain distinct gates.
- A CMD entry without PowerShell 7 retains its previous Python route. The
  shell-host capability check uses CMD's built-in PATH expansion rather than
  adding a discovery process or a new mandatory dependency. Native missing-host
  and CMD handoff/selected-slot controls pass.

### 2026-11-01 - Published review corrections

- #9936's first review identified three concrete gaps: the shared wrapper
  cleanup roster omitted the new helper; a standalone CMD replacement fixture
  omitted its companion PS1; and a caller-defined `ERRORLEVEL` could shadow the
  terminal-host result. All three are corrected before accepting the review.
- Deployment/uninstall tests now derive owned files from the actual manifest,
  exercising removal of the helper rather than repeating an incomplete tuple.
  The CMD replacement cases include their companion PS1 and a deliberately
  shadowing `ERRORLEVEL=0`, covering both retained and constructed runtimes and
  real nonzero return.
- The two deployment/uninstall controls and four native CMD replacement/exit
  cases pass. Review and required CI must still clear on the updated head.

### 2026-11-01 - Reused shell environment isolation

- The next review reports no open inline issues but its overview identifies
  reused-shell environment isolation. That is a real effect of replacing a
  child PowerShell with in-process script invocation: restoring only the three
  prepared variables would leak unrelated launcher additions/removals/changes.
- The foreground helper now snapshots the process environment in memory before
  applying the selected launch state and restores the complete snapshot on
  return. No environment snapshot is serialized, logged or sent elsewhere.
  This adds two environment enumerations per supported foreground launch, not
  recurring work or another process.
- The native selected script deliberately adds, removes and changes environment
  values; every returning entry route must restore the original state. The final
  12-case ConPTY/real-mux packet passes, including production refusal and
  concurrent detach/rejoin.
- Native process attribution additionally checks retained creation times before
  joining Toolhelp parent PIDs, excluding older processes whose original parent
  PID has been reused. Production launch adds no process census.

### 2026-11-01 - Explicit environment selectors and native error policy

- The next review overview identifies an environment-selected no-mux path and
  in-progress JSON marker reads, despite having no new inline threads. Both
  preparation and observation gates now honor the launcher's exact
  `WORKTREE_NO_MUX=1` selector. Its legacy contained child path and actual return
  observations remain intact; `WORKTREE_NO_MUX=0` still permits mux handoff.
- The helper locally retains native exit-code semantics instead of inheriting
  a caller's native-error-as-exception preference. Native fixtures now enable
  that preference with terminating error handling and require the original
  preference to survive return, including nonzero preparation and the real
  production launcher's refusal.
- Test marker readers retry incomplete UTF-8/JSON and transient sharing errors
  within their existing deadline, surfacing the last error on expiry. The mux
  resize marker uses the same complete-record reader.
- All 12 native entry/mux cases pass under inherited error-as-exception policy.
  The expanded 29-case producer/observation unit packet passes, including exact
  environment selector 1/0 behavior and non-required post-effect observations.

### 2026-11-01 - Admission revision held for decision

- The next overview identifies inherited stale channels and literal empty
  preparation arguments. The empty-value binding defect is independently
  corrected with `AllowEmptyString`; a real ConPTY preparation call preserves
  empty, quoted and Unicode values and native exit 7.
- Tracing confirms that resident startup/control helpers otherwise inherit the
  foreground environment. Existing new-worker filtering does not establish
  compatibility with an older runtime that never recognized the channel.
- The source PR is held as native draft, not merged or abandoned. The admission
  alternatives above are recommendations pending a decision; no capability
  probe or installer-contract revision has been implemented or authorized by
  this record. Existing source/native results and all live/deployment limits
  remain preserved.

### 2026-11-02 - Query direction selected

- Captured the exact `probe` answer and selected label in Scope Approvals.
  The query direction is selected; its concrete receiver-admission, finite
  cleanup and foreground-only transport design remains subject to the source
  review gate.
- No capability-query implementation, runtime installation, service restart,
  live-session migration or hold change has been performed.

### 2026-11-02 - Admission amendment proposed

- The original plan cleared review in #9913. Isolated implementation work
  exposed the unsupported-peer environment-inheritance flaw described above.
- Captured the exact `probe` selection and its source-only bounds. The query
  amendment is proposed before implementation, not proof of an implemented
  capability surface or completed qualification.
- The broader parent gates and the #8669 owner's scope remain unchanged.
  No installed runtime, live session or deployment hold has been changed.

### 2026-11-02 - Admission amendment reviewed

- #9943 merged after an approval at its exact source head, required green CI
  and no open review threads (merge `8efd18c2fae5`). Its selected-runtime query,
  foreground-only propagation and finite-cleanup obligations govern the next
  implementation slice.
- The implementation remains drafted. This completes the admission-plan review
  gate, not capability implementation, source qualification, release or rollout.

### 2026-11-02 - Selected-runtime admission qualified

- The shell queries the selected native runtime through an early, effect-free
  `--version` branch before offering the foreground channel. The existing
  finite process owner limits each stream to 4096 bytes and target execution
  to ten seconds, settles its child tree and closes the private transient
  transport before returning. No new daemon, installer, network bootstrap,
  Task or UAC path is introduced.
- Worktrees queries its independently selected Manager before forwarding.
  Manager returns its isolated module command and scoped state root;
  Worktrees executes that pinned command rather than resolving the front door
  again. A native test advances the selector to a legacy receiver immediately
  after the query and still reaches the queried runtime. Removing completion
  after selection instead fails at the existing slot-local retention bootstrap
  before receiver execution, with no fallback or second session.
- Current/legacy, both mixed-version directions, malformed/nonzero/stale
  responses, repeated offers and both Worktrees front doors pass the isolated
  native controls. The 23-case native packet includes the selector race and
  query/execution lease gap; the 233-case Worktrees packet covers admission,
  ordinary routing, resident bootstrap and provider-session entry. Earlier
  owned-output, overflow and timeout controls pass under PowerShell 5 and 7.
  These packets overlap; their counts are not additive coverage.
- The final direct query sample measured 1384.30 ms cold and 777.30 ms warm.
  Birth-pinned polling observed three Python processes and two console hosts
  per query, all retired before admission. These are lower bounds: short-lived
  trampolines may fall between samples. Exhaustive transient attribution
  remains open; the supported attachment still retains zero Python processes
  in either forwarding role. This is not a zero-discovery claim.
- Fresh synthetic numbered runtimes and native terminal boundaries exercise
  the source contract without touching installed runtimes. A full real-engine
  Copilot launch, installed singleton inventory and replacement survival are
  not exercised here: authorized installed proof remains with parent Phase 4 /
  #5579, and the existing #8669 owner keeps its copied-shell/lifecycle scope.
  Source review, required CI, promotion and installed rollout remain distinct.
  No runtime was deployed, live session changed or deployment hold lifted.
- Reconciliation with current `dev` retained the upstream private-inventory
  creation owner and regenerated its canonical helper archives; no source
  implementation conflict was resolved by choosing an older binary. The
  87-case finite-owner/private-inventory packet and all 23 native cases pass
  on that merged archive. Its later direct-query sample measured 1003.73 ms
  cold and 543.34 ms warm with the same observed lower-bound process roles.
- The newer shrink-only module ceiling required the early Worktrees query
  helper to own its successful exit directly. Its real module-entry regression
  refuses ordinary roster/CLI imports and passes with the query packet
  (13 cases); the entry module stays at its existing ceiling without widening
  the baseline.

### 2026-11-02 - Exact-head review and CI corrections

- Review #9936 identified three actionable gaps. The fixed resident selector
  is now excluded from `--version` and restored only for pinned execution;
  configured relative Manager roots resolve against the query cwd. A native
  fast-entry control exercises the real Worktrees dispatch with a synthetic
  discovery response and requires the selected receiver to see the exact
  resident/project arguments.
- Replaced the former bypassed CMD/PS1 fixture parameters with a direct
  generated-CMD ConPTY entry. Its real terminal host and command-shell shim
  are classified separately; all preparation Python descendants retire before
  literal arguments, input/resize and exit 23 pass. The test's outer PowerShell
  wrapper captures that native exit under its own explicit preference; this
  does not claim a CMD child can change its caller's error policy.
- The Worktrees query/resident packet passes 44 cases. Manager's producer and
  native packet passes 55 cases with the direct-CMD wrapper correction passing
  separately; these overlapping packets are not additive counts.
- CI exposed omitted installation-context copies of the canonical native
  caller archive and the changed shell source's stale unit admission digest.
  Synchronized all fourteen dependent archives, retained the MCP footprint
  contract revision, added the installation-context changefile, and refreshed
  only that reviewed source binding. Existing actor-free unit nodes and their
  effect guards are unchanged; native query/terminal actors remain excluded.
- A full local admitted-unit attempt reached 1933 passing cases but could not
  qualify the packet on this Windows token: existing fixture symlink creation
  needs a privilege it does not hold, and the packet also reported platform
  skips. No privileges, fixtures or unit gates were weakened. Required Linux
  CI must run the exact new source packet before merge; partial local output
  is not a passing unit-evidence claim.
- The three review findings, required CI, source merge, promotion and installed
  proof remain open until their respective owners confirm them. No installed
  rollout, live-session change or hold change occurred.

### 2026-11-02 - Preserve newer source contracts through rebase

- Rebased onto the newer typed bootstrap and peer-launch source. Regenerated
  both runtime and installation-context archives from the merged canonical
  sources; retained the MCP footprint revision and upstream unit admissions.
- The merged source passes 74 finite-process owner cases, 56 Manager query and
  native terminal cases, and the 164-case Worktrees launch packet. Corrected
  the direct-fallback expectations to preserve the existing external bytecode
  cache arguments rather than asserting a cache-free interpreter command.
- The native query measured 1528.68 ms cold and 856.47 ms warm. Its birth-pinned
  sample observed five Python and five console-host processes across both
  queries; this remains a lower bound, not exhaustive transient attribution.
- Exact-head required CI and review must still qualify publication. The full
  local unit packet's previously recorded privilege/platform limitation and
  the no-rollout boundary remain unchanged.

### 2026-11-02 - Close post-admission failure paths

- Review identified a fast-entry spawn failure that could return to ordinary
  dispatch after a Manager had already been admitted. An active foreground
  offer now reports that failure explicitly instead of resolving a second
  runtime or opening another Picker. The no-offer compatibility path remains.
- Moved the shell's finite completion observer into the attachment's cleanup
  path. A throwing launcher now records failure before original terminal
  environment and location are restored; it cannot leave an admitted attempt
  without its return event merely because the script raised an exception.
- Native regression cases remove the actual admitted executable after the
  query, preserve the no-offer fallback, and throw from the selected shell
  script while checking the recorded failure. These cases and the existing
  terminal handoff packet pass without installed runtime changes.
- A subsequent review identified expired offers reaching effectful resolution
  before publication validated them. The same request validator now runs at
  native/ordinary entry and immediately before launch resolution, while
  publication revalidates it. Missing, malformed and already-consumed offers
  cannot reach engine acceptance or worktree allocation. The combined query
  and native packet passes 62 cases; focused admission cases pass 36.
- Closed the same admission gap at the Worktrees front door and before
  receiver queries, including legacy receivers, using one shared read-only
  offer validator rather than diverging parsers. Preparation also publishes
  the absolute Manager journal root so completion cannot reinterpret a
  relative root after the terminal restores its original working directory.
- Required Linux unit CI exposed the SDK fixture's explicit package closure
  omitting the new query module. Added both new shared modules to that closure
  and refreshed only its reviewed source admission. Its 170 focused cases
  pass; the Worktrees admission packet passes 41 and Manager query/native
  packet passes 63. Full required unit evidence still awaits the new head.
