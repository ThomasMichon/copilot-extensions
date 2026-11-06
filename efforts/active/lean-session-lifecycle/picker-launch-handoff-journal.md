# Picker Launch Handoff - Journal

[Back to the mini-effort](picker-launch-handoff.md).

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

### 2026-11-02 - Merge-time native API reconciliation

- Required CI passed on `705dd3976670`, including eligible units, guards/lint
  and the PR gate. Its exact-head Copilot review reported no open inline
  findings; all ten threads were resolved. Recorded the shared-validator,
  isolated pinned-runtime and absolute-journal corrections against the
  overview's repeated failure classes before attempting the managed merge.
- The merge did not occur: the base advanced to #9985 during the operation.
  Rebased onto its native platform API typing, retained the current footprint
  contract revision and regenerated both canonical archive families. Only
  generated MCP producer digests required conflict resolution and refresh.
- The rebased SDK observer/process/functional packet passes 247 cases, and
  Manager preparation/native terminal handoff passes 63. The latest query
  sample measures 1024.41 ms cold and 739.32 ms warm; its six Python and four
  console-host observations remain a birth-pinned lower bound, not exhaustive
  attribution. No installed runtime or live session was changed.
- This new head still requires its own review and CI before source merge;
  the old head's green result is not carried forward as exact-head evidence.

### 2026-11-02 - Source implementation merged

- #9936 merged at `bea5b3fff3c9261ef77c4ec07fe207c950739a5f` after exact-head
  required CI `41450218957`, review `5737068171`, all ten resolved threads and
  the recorded assessment of its repeated overview. The managed merge used
  the repository's owner review exception, not a CI bypass.
- Post-merge `dev` CI `41451356628` passed. Release promotion is pending;
  a merged contribution and a green trigger are not release availability.
- Re-read this mini-effort and the parent after merge. The source/regression/
  documentation slice is complete; exhaustive transient-query attribution,
  release availability and authorized installed singleton/running-state
  qualification remain unchecked. No rollout, retained launcher retirement,
  live-session migration or deployment-hold change was performed.

### 2026-11-02 - Release and authorized adoption

- Promotion #9992 merged Main `59aef9f0bf6a8f0f40215a92c743ee68dd15a54d`
  from the qualified #9936 source. Run `41452050809` passed qualification,
  source publication/readback and exact candidate merge. The five handoff
  shell/module blobs matched the merged source. Release availability is
  distinct from installed adoption; #5579 retains the release journal.
- The operator subsequently authorized an as-needed ordinary local update and
  continuation of this effort. That does not authorize terminating retained
  sessions, weakening Job policy or taking another writer's work.
- The first update's monitor health check rolled Worktrees back; its
  interrupted receipt was retained and released through the supported
  operator-repair exclusion, without claiming native cleanup or replaying it.
  The next ordinary update completed Worktrees `1.24.419-dev1`'s transition
  and Manager `0.6.252-dev1`'s activation, but its final suite child failed
  implicit project resolution from staging. That failed whole-suite result
  is not recorded as successful adoption.
- #10079 owns the newly observed context regression. The source correction
  applies the already-captured caller directory to the engine update child,
  preserving durable queue/source identities and other verbs' cwd. Invalid
  captured directories fail before child execution. An explicit-project
  ordinary update is separately progressing through the existing updater;
  its result and the new source correction's release remain separate gates.
- The focused Manager/update/constructor packet passes 215 cases with five
  existing platform skips. The new isolated real-child cwd/argv/exit control
  passes separately. Constructor units reuse the synthetic constructor's
  custody fixture; real queue-worker controls now own a private registry,
  renewable test lease and checked cleanup, and use the installation-owned
  observer boundary for their deliberately surviving worker. Production Job
  policy and contained-test suppression are unchanged.
- This is a below-altitude correction of the existing invocation-context
  contract, not a new provisioning design. The real-child control exercises
  the fresh caller-versus-staging boundary without another full installation.
  Required exact-head CI/review, publication of #10079's fix, successful
  whole-suite adoption and actual new-session shape remain open. No external
  venue is substituted for the local Windows consumer being qualified.

### 2026-11-02 - Adoption blocker correction qualified

- The cwd correction lives in the existing update handoff adapter, retaining
  the engine client's source-module ceiling without widening its baseline.
  Update/request tests pass 136 cases with three existing platform skips.
- The complete uninstrumented native context selection passes all 12 cases.
  Its repair fixture now borrows its already-held real update custody rather
  than deadlocking on a second queue drain. Native installer calls retain
  finite child/stream ownership at their original 30/45-second deadlines;
  the unowned control explicitly omits witness export. Routing assertions
  retain exact interpreter and trailing arguments without expecting two
  independent unsealed-cache nonce allocations to match.
- Earlier native selections exposed cold scoped-repair timeouts; the final
  complete selection passed without increasing those deadlines. These native
  cases are not promoted into actor-free unit evidence. Source admission,
  module-size and installation-contract checks pass; exact published-head
  CI/review are still required.
- The explicit-project whole-suite update reached its terminal failure result
  after core adoption: Bridge timed out during cutover and Index during build.
  The installed core selection is not evidence that the full requested update
  succeeded. Owner-scoped health inspection and repair remain open alongside
  new-session qualification; no retained session is terminated to hide them.

### 2026-11-02 - Review corrections and owner-scoped recovery

- #10100's exact-head review identified invalid caller context reaching durable
  submission and an older two-argument handoff test double. Shared validation
  now rejects invalid normal requests before enqueue/worker startup, including
  detached observation, and rechecks restored request context and final launch.
  The scoped-source regression uses the real handoff adapter instead of the
  obsolete double. The affected packet passes 177 cases with three existing
  platform skips; the real child-directory control passes separately.
- The scoped updater repaired the Index client without activating a host,
  replacing its durable engine or reindexing data. Bridge's retained aborted
  cutover was inspected and reconciled through its own recovery operation;
  no marker was edited manually and no existing agent session was recreated.
  The configured service startup subsequently published the selected current
  daemon, whose routed process identity and readiness were observed after
  its startup caller had exited. Remaining installer reconciliation and
  complete working-set verification are still separate obligations.

### 2026-11-03 - Context correction merged; watchdog blocker isolated

- #10100 merged at `06e17474ee8ca66d9bfb92b81fe385f43ec5e00d` after
  exact-head required CI, resolution of all three review threads, the
  documentation/contract assessment and the recorded clean-Comment
  evaluation. The source worktree is pulled forward; release and adoption
  of this additional correction are not inferred from that merge.
- The Bridge survivor later requested watchdog replacement after health
  timeouts. Its current-generation replacement failed daemon-instance PID
  admission. #10121 owns the isolated service-watchdog defect: the already
  attributed delayed helper uses Windows CRT `exec`, which starts a different
  process while carrying the helper's instance context. This is separate
  from the external contributor's installer-watchdog work in #6928.
- The next bounded source stretch preserves the admitted helper PID on
  Windows, retains existing POSIX behavior and qualifies actual native
  admission. No admission check, Job policy, deadline or session database is
  bypassed. A proposed terminal-metadata repair was not performed because
  its required healthy-survivor identity could no longer be established.
  Whole-update recovery and the parent completion gates remain open.

### 2026-11-03 - Watchdog PID custody qualified at the source boundary

- #10121's Windows replacement now enters the module in the admitted helper
  process, preserving its PID, original start flags and delayed startup.
  POSIX keeps its existing `exec` path. The exact canonical Windows helper
  also joins strict daemon recognition so ensure/retirement does not mistake
  the repaired replacement for an unrelated `-c` process; arbitrary snippets,
  changed helper bodies and non-start commands remain refused.
- The focused watchdog, process-identity and launch-adapter packet passes
  95 cases with nine existing platform skips. Its native control uses the
  actual attributed launcher and decorated service admission, with only the
  serving body replaced by PID/argv evidence under a private renewed test
  lifecycle. The initial fixture's command-registration cycle was corrected
  without mocking admission or changing process custody.
- Python lint, the complete module-size check and the 13-plugin installation
  contract pass. Architecture and test documentation describe this bounded
  evidence; no service port or existing session is used by the fixture.
  Exact-head review/CI, release, authorized adoption and live watchdog recovery
  remain open. Neither the mini-task nor its inherited parent is complete.

### 2026-11-03 - Watchdog source repair merged; adoption still open

- #10142 merged at `cfb0a34c174e6d66bee7e01645924e1c5aee2619` after
  an actual exact-head approval, no unresolved review threads and green
  required CI. No native admission or CI gate was bypassed.
- Re-read this mini-effort and its parent after merge. The remaining work is
  release and authorized installed recovery, exhaustive transient-query
  measurements and live shared-daemon singleton evidence; Phase 2's bounded
  pending-work retries remain open independently.
- #10100's context correction is now released in #10134, with both changed
  production-module blobs verified against the promoted snapshot. This is
  release availability, not adoption. The earlier oversized coverage evidence
  failure was fixed separately by #10129; do not duplicate that owner's work
  or misreport a successful retry as the original failed run passing.

### 2026-11-03 - Released repair; independent adoption progress blockers

- #10142 is released in #10145, with both changed production blobs matched
  to the promoted snapshot. The ordinary update still cannot select the new
  Manager: its installed candidate preparation reaches the existing allowance
  while copying/verifying inventory. #10126 already owns that producer
  correction; the reproduction was added there rather than opening a
  competing implementation or increasing its deadline.
- The separately scoped Bridge update also failed before payload publication.
  Its native CLI staging directory was still creating files at the timeout
  boundary, with roughly half the incumbent payload's file count copied.
  #10159 records this distinct external-producer progress gap. Partial staging,
  confirmed child cleanup and a released source fix do not establish adoption.
- A read-only native marketplace command completes despite the same adjacent
  OAuth-login diagnostic seen in the failed child. That diagnostic is not
  established as the copy timeout's cause; no credentials, authentication
  policy, timeout, installed code or coordination marker was changed.
- The selected installed Manager `0.6.252-dev1` independently answers the
  effect-free terminal capability query through the actual registered front
  door. Its response pins the selected isolated module command and root;
  the existing finite owner returns zero with no stderr. This qualifies only
  selected-runtime admission, not a real attachment, exhaustive transient
  inventory, shared-daemon singleton or successful whole-suite update.
- Keep the authorized rollout and live qualification open behind these
  tracked blockers. The parent's bounded pending-work/retry slice remains an
  independent authorized avenue; no existing session needs to be terminated
  or migrated to pursue it.

### 2026-11-03 - Acquisition progress decision prepared

- The independent idle-path source correction merged in #10167; its parent
  journal records the exact review/CI outcome and remaining live gates.
- #10159's opaque native copy has no captured completed-unit diagnostics.
  The current finite stream owner supports both inactivity and absolute
  deadlines, and Windows exposes write accounting on the already-held
  containment Job. Neither observation is an implemented correction.
- **Agent-recommended proposal; operator decision pending:** admit strictly
  increasing completed native write-byte counts from that exact owned Job as
  activity for Windows payload acquisition, while retaining the existing quiet
  allowance, acquisition target budget, strict child-tree settlement, source
  admission and publication checks. Do not renew from a clock, CPU use,
  read-only activity or an unchanged counter; accounting failure must remain
  explicit. Native tests must cover useful silent writes, a subsequent stall,
  read-only work, the absolute ceiling and descendant cleanup.
- The alternative is to retain output-only observation and pursue completed
  copy diagnostics in the native Copilot producer. That is a separate upstream
  contribution boundary, not permission to redirect this report or file an
  unreviewed upstream issue.
- A consumer-side correction also needs a qualified bootstrap/adoption path:
  no fix is live merely because its source is released, and the incumbent must
  not be monkey-patched to acquire its own replacement. The existing update
  owner was asked only about already-established bootstrap readiness, with no
  new update, recovery, patch or ownership transfer requested.

### 2026-11-03 - Consumer direction selected

- Captured the operator's exact `consumer` response and selected label in Scope
  Approvals. Slice 4 proposes the bounded consumer correction and native
  validation before implementation; it has not yet cleared source review.
- The installed-acceptance owner reports no already-qualified consumer-first
  bootstrap: the known unified path acquires payloads before runtime installation.
  No alternate bootstrap, incumbent patch or acquisition replay is qualified.
  Both owners have no update or recovery in flight or pending. The source plan
  preserves this adoption gap instead of presenting the proposed observer as
  an immediately deployable repair.
- A later operator direction assigns one new normal update, after read-only
  admission, to the existing installed-acceptance owner. This source driver
  will not run a concurrent update. That attempt's outcome is unobserved and
  remains separate from the proposed correction.

### 2026-11-03 - Consumer prerequisite plan reviewed

- #10178 merged at `99d8fef514e5cd7c0523e7c4aa05d31b8c58c6bd` after
  exact-head Copilot approval, no open review threads and green required CI
  `41585832546`. Pulled forward and re-read the parent and mini-effort gates.
- Only the proposal-review item is complete. The next source slice implements
  the optional held-Job activity observer and unchanged absolute acquisition
  budget, then qualifies its native failure/cleanup controls. No observer code,
  installed adoption, bootstrap workaround or live qualification is claimed.

### 2026-11-03 - Native acquisition activity

- Implemented the reviewed held-Job write observer without changing quiet/absolute budgets, output defaults or custody; source review and release remain pending.
- Isolated Windows writer/stall/read-only/absolute/error controls and native output/receipt parity pass; actor-free counter/clock and activation/auth/retry controls remain separate evidence.
- The registered-origin bootstrap consumer retains its separate absolute 180-second ceiling, not an inactivity clock. It remains in the bootstrap integration inventory; no installed adoption is claimed.
- #10198 merged at `ef245396386d92dc9d10c2f90ac8afe7a7f11bcc` after actual Copilot approval, required CI `41608503666` and native caller controls `41608498374` passed.
- Release/bootstrap, installed qualification and the parent gates remain open and paused at the operator-directed winddown; no additional rollout was started.

### 2026-11-04 - Resume the charter and cover flagged CMD entry

- The operator resumed the original launch-lifecycle charter. The separately
  owned cold-resume liveness correction is not part of this source slice.
- Selected-consumer inspection now finds #10198's three updater modules
  byte-identical to the reviewed correction, and the native write observer is
  present. This is installed-code evidence, not proof of a successful complete
  update or the separately supervised bootstrap consumer.
- Live observation found that ordinary PowerShell launches no longer retain
  the two Python forwarding roles, but a CMD resident-picker launch still
  retains both Manager interpreters after attachment. The generated CMD entry
  only selected its terminal-owned sibling for an empty invocation or an
  otherwise argument-free project selector.
- Route all non-inherited CMD calls through the existing PowerShell entry
  when it is available. That entry retains ownership of redirected-input,
  selected-runtime capability and legacy-peer decisions. Inherited offers,
  the missing-PowerShell fallback and same-line batch exit handling are
  unchanged; there is no second argument parser or resident helper.
- The new actual generated-CMD ConPTY control reproduces the missing offer on
  the old source. Its x64-on-ARM cleanup encountered the already tracked
  executable mapping in #7643; a read-only native query identified the external
  cache holder. No service, process, ACL or containment policy was changed.
- Native ARM qualification passes 37 terminal contract cases and seven
  generated-CMD controls, including resident-picker arguments, attachment with
  zero surviving Python workers, terminal input/resize, return receipts,
  missing PowerShell and replaced-launcher exit handling. Both contained
  packets finish successfully with their ordinary cleanup and limits.
- An earlier broader packet reached its unchanged 300-second group limit and
  reported persistent PATH drift without attributing its writer. No rollback
  was performed. The focused packets above retain the original limit and
  report neither drift nor cleanup failure; a whole-plugin pass is not claimed.
- Source publication, release, actual installed flagged-CMD acceptance,
  exhaustive capability-query inventory, bootstrap/updater completion and the
  parent handoff/singleton measurements remain open. No retained session was
  terminated or migrated.

### 2026-11-04 - Whole-suite activation prerequisite

- The existing hosted Windows whole-suite lane at source
  `1eea3e286c32f4f09ddb9b560b88b5bc3f4347eb`, run `41797313035`, fails
  baseline preparation in both normal and repair modes. Vault activation
  reports a configuration/owner resolution refusal; neither mode reaches
  replacement assertions. Both fixture settlements succeed.
- #10397 tracks this bounded prerequisite. Its underlying cause remains
  unestablished; an invalid owner or configuration must not be reclassified as
  inactive to get a green result. Explicit opt-in and native ownership remain
  unchanged.
- Added a failure-only, bounded read of the retained fixture payload. It records
  caught activation exceptions and a refused configuration owner's bounded
  stderr without changing the reader or original failure. The actual-process
  control passes with exit 3, unchanged reader bytes and ordinary cleanup.
- A changed-input native run is collecting the missing diagnostic evidence.
  This is investigation, not a production Vault fix or a passed whole-suite
  gate. No deadline, installer, service or configuration policy was bypassed.
- That exact-head run, `41802608780` at `ddb5d594bad08837ff4d2313cf7d103ad6f09917`,
  also fails both preparations and settles both fixtures. The post-failure
  reader replay returns inactive (exit 2), not the original refusal; it does
  not establish the failure's cause or qualify a fix. Preparation-time
  environment/interpreter and owner-readiness differences remain unresolved.
- Retain bounded, value-free exception locations in the original activation
  reader's failure report and preserve that report through the Windows
  adapter. Actual Python, PowerShell 5.1 and PowerShell 7 controls pass for
  invalid configuration, without exposing its value or installing a runtime.
  Native ARM test preparation cannot build the existing cryptography extra
  without OpenSSL; the existing x64 interpreter runs these controls under
  the ordinary contained runner with successful cleanup.
- Address the diagnostic-preservation review finding: unavailable retained
  input and settled probe timeout/output overflow become secondary diagnostics.
  The original assertion and `check=False` outcome remain intact; unconfirmed
  execution or cleanup still propagates with its exact custody. Eight focused
  controls pass under the canonical contained native runner, including the
  real reader and retained cleanup-error identity.
- Run `41806293228` reaches the original failure report in both modes:
  the qualified Worktrees configuration peer returns nonzero inside
  `read_owner_context`; the outer reader returns failed/3. Its later replay
  remains inactive/2, so the original peer's stderr is still needed.
- Temporarily instrument only the disposable fixture's acquired Vault reader
  to capture that original peer result. Preparation explicitly refuses
  acceptance even if the instrumented update succeeds. Remove this trace and
  refusal before final qualification. Nine focused controls pass, including
  actual acquisition plus reader execution with unchanged failed/3 output and
  the controlled peer diagnostic; the ordinary runner settles successfully.
- Before dispatching that instrumented reproduction, the independent updater
  context owner supplied a narrower CWD reproduction on #10397. That owner
  retains updater context propagation/tests/docs; this slice retains Vault
  diagnostics and the Manager qualification lane, with no overlapping updater
  edits. Remove the temporary trace, deliberate refusal and its control
  without running another instrumented VM. Final acceptance must use ordinary
  fixture delivery after the independently reviewed updater prerequisite.
- Make the whole-suite fixture's Vault participation explicit in its
  disposable project (`enabled: true`). The lane expects a real Vault resident;
  plugin enablement alone must not supply that opt-in. A real payload-reader
  control proves configured activation from the fixture's repository without
  installing a runtime. The nine-control packet passes with ordinary cleanup;
  inactive defaults and operator configuration remain unchanged.
- Integrate the independently reviewed updater-context prerequisite #10416
  after its verified merge at `3938466f7586810d7b15a5be109febc661e66996`.
  The ordinary native candidate now combines that source with the explicit
  disposable Vault opt-in and safe failure diagnostics. No temporary trace
  or intentional acceptance refusal remains. Repeat the real normal/repair
  portfolio; source review or a merge is not combined acceptance or deployment.
- The ordinary, uninstrumented Windows portfolio `41814631845` passes at
  exact combined head `7f3ec8b90989bffafde2de0237fe74af8f99909d`. Both normal
  and repair retain prepare/exercise/assertions/settle exit 0 and empty errors.
  The earlier original-boundary refusal is corrected without converting
  invalid configuration into inactive readiness or weakening custody.
- This journal receipt changes no packaged code, fixture or workflow from
  that qualified head. Current source review/CI, release and separately
  authorized installed proof remain independent gates.

### 2026-11-04 - Released Manager and installed query inventory

- #10388 merged as `5410f14a37f1524413bc535c64ee80f06a77a7d3` after
  current-head Copilot approval, required CI and the ordinary native
  normal/repair portfolio above. The released Manager `0.6.284-dev1` is
  selected through the authorized normal bootstrap; its generated CMD entry
  contains the flagged-argument correction. Its canonical daemon reader
  reports one active, ready daemon on that version. This is not yet a new
  installed CMD attachment or concurrent-launch qualification.
- The subsequent engine-repair phase refused a same-version Worktrees
  `1.24.462-dev1` content conflict. The canonical staged and delivered input
  hashes agree with each other but differ from the selected slot's completion
  hash. The cause of those different inputs is not established. The resulting
  request is interrupted with unconfirmed transaction custody, not successful
  or cleaned up. Preserve #10377's existing upgrade-recovery ownership.
- A separately approved official queue release retained that failed history
  and claimed no historical cleanup. A fresh request through the supported
  standalone update entry also exits 1. Worktrees, Bridge, CodeSpaces,
  Containers, Dispatch, Index and Logger report completed component updates;
  the log ends during Machines construction without a final component
  outcome. Its original failure cause and remaining effects need diagnosis.
  Neither component progress nor the successful Manager bootstrap establishes
  full-update completion; no automatic replay is licensed by this result.
- Measured the actual selected Manager `0.6.284-dev1` and Worktrees
  `1.24.462-dev1` capability queries through the installed terminal helper.
  Each component's first query in a fresh terminal host and its warm repeat
  created exactly five transient native Job members: the finite query owner,
  the selected venv launcher, its base interpreter, and two console hosts.
  Birth-pinned executable-image observations account for every process in
  the Job's cumulative count. Every child exits before return; the terminal
  root then exits and its caller-owned Job closes. No resident was started.
- Image-qualified elapsed times were 1,561/1,282 ms for Manager and
  1,574/1,386 ms for Worktrees, first/warm respectively. Native Job CPU was
  1,359/859 ms and 1,172/672 ms respectively. These are bounded observations,
  not latency guarantees or a claim of zero discovery work. First-query
  means a new terminal host, not an uncached OS or a fresh installation.
- Query inventory does not close the remaining installed attachment,
  full-update, concurrent-session or handoff gates. The earlier monitor stack
  samples were explicitly not CPU percentages; do not turn this query CPU
  measurement into an unsupported before/after monitor CPU comparison.

### 2026-11-04 - Native Manager observation prerequisite

- The continuing driver explicitly adopted a worktree-owned execution agenda.
  Its ordered launch, handoff and completion work retains this canonical
  effort's full gate and independent obligations. A necessary Manager reader
  prerequisite saves its installed-adoption return; it does not replace the
  parent objective or authorize another installer.
- A second ordinary observation of the same daemon reports unverified-owner;
  the next canonical read raises the ten-second CIM census timeout. The
  daemon's native incarnation remains unchanged. This invalidates a
  two-cycle healthy-singleton claim, not the earlier individual ready result.
- Claimed the bounded read-only correction on #5579 in
  [comment 6642376457](https://github.com/ThomasMichon/copilot-extensions/issues/5579#issuecomment-6642376457).
  Replace this diagnostic reader's Windows census, command line, owner SID
  and birth lookups with native observations. Preserve current-user, root,
  incarnation and post-health checks; cutover and retirement mutation helpers
  remain unchanged. This closes the existing transient-observation intent,
  not a new lifecycle authority.
- Real native command-line/SID/birth reads succeed from both ARM64 and x64.
  A fresh corrected-source consumer additionally exposes an indirect
  platform-discovery subprocess in the shared birth helper; use the existing
  native identity primitive directly on Windows instead. No installed
  runtime or retained session is changed.
- The final contained status/CLI/refusal/native packet passes 42 tests with
  successful ordinary cleanup. A read-only corrected-source observation of
  the actual selected daemon returns ready in 141 ms with subprocess probes
  forbidden and the same native birth identity. This is measured source
  consumer evidence, not a latency guarantee or installed adoption.
- Source review, release, actual installed reader and CMD attachment,
  full-update failure reconciliation and the parent gates remain open.

### 2026-11-04 - Native reader source merged

- #10514 merged as `d9fe83390433f03da6bed344fdfac03b9b46d0a7` after
  Copilot approval at `b70202abd21c2592356302eab66c7b558d102cd8`,
  no review threads, and green required CI. Recording the external receipt in
  the PR body triggered another CI run; the first merge correctly refused its
  then-expected required gate. The later merge waited for that run to succeed.
- External portfolio `41919470838` covers the same exact head. Its Windows
  native Companion/reader step and Linux Companion native job both succeed.
  The new reader packet is selected explicitly, not inferred from unrelated
  installation tests or a skipped native case.
- Re-read the parent and mini-effort after merge. No installed, singleton,
  launch or handoff checkbox closes from this source result. The saved
  continuation now observes promotion and the separately authorized installed
  reader, then returns to the failed full-update and launch qualification.

### 2026-11-04 - Publisher permission boundary

- Release run `41920408755` has one failed job, promotion `126896518220`.
  Admission, frozen candidate, source units, construction and artifact
  attestation succeed. GitHub refuses the candidate branch push because its
  configured publication credential lacks workflow-write permission.
  #10515 already tracks this exact signature and administrative-owner action;
  this is distinct from #10432's earlier missing artifact.
- The workflow selects its existing publisher credential at the push step and
  disables persisted checkout credentials. No source test correction or
  unchanged retry supplies the missing permission. Do not copy another
  account's credential, change publisher identity, weaken branch gates or
  claim release from the merged source.
- The bounded decision is whether the credential owner restores only the
  existing repository-scoped publisher's ability to publish reviewed workflow
  changes, preserving its actor and source policy, or retains the permission
  hold. Credential values are not requested in conversation or published.
- After owner-confirmed repair, re-inspect the actual release and use the
  documented exact-candidate recovery where needed; a confirmation is not
  itself a completed promotion or installed rollout. Until then retain this
  release branch and all parent completion gates while continuing permitted
  independent diagnosis.

### 2026-11-04 - Operator-requested installed cold-roster check

- The operator explicitly selected stopping the local Worktrees status daemon
  and timing the ordinary CLI read. The selected installed runtime is
  `1.24.464-dev1`. Only the authenticated status-monitor shutdown was requested;
  no session or mux process was stopped, and no lock was removed manually.
- The shutdown RPC exceeded its ten-second response timeout. A separately
  retained native process-incarnation check subsequently confirmed exit.
  The timed CLI invocation therefore starts after confirmed exit, not merely
  after a shutdown acknowledgment.
- The first `list --current --json` returns exit 0 with 50 rows in 2.996
  seconds. That is retained advisory continuity, not completed recovery:
  the old endpoint still refuses connections. A subsequent 3.317-second read
  explicitly reports advisory quality and `unready: project cleanup pending`.
- Normal owner recovery starts a replacement. A later 3.757-second read
  returns 50 rows with current quality, a new publication generation and no
  availability warning. Authenticated health confirms the replacement ready,
  published and serving a ready current roster.
- These observations establish first-return latency and eventual recovery for
  this installed test. The gap between observations prevents an exact
  stop-to-current-publication latency claim. Returning cached rows quickly is
  not evidence that the initial current-roster readiness gate is fixed.

### 2026-11-04 - Blocking JSON list requirement

The operator's follow-up requirement, verbatim:

> We need to not expose this error unless there is a timeout. When any caller asks for `agent-worktrees list --json`, even if the daemon is not running, it should just block and wait until the daemon can be queried for status. The caller can have its own timeout if it cares

The bounded correction waits for the selected resident's usable publication
without an implicit overall CLI deadline or advisory success. Keep individual
transport bounds, cancellation, strict protocol/authentication failures and the
existing startup owner; do not compute a roster in the caller, release custody
or label retained rows current. Existing explicitly selected UI bootstrap
previews remain a separate contract. Qualification covers a genuinely absent
resident, temporary producer contention, caller cancellation/timeout and a
healthy current publication. This prerequisite retains the parent effort and
its saved installed-update, launch and handoff obligations.

- Implemented the wait at ordinary JSON/one-shot-stream CLI admission and
  before a non-bootstrap feed's first atomic publication. Existing bounded
  programmatic and explicit saved-preview bootstrap reads remain unchanged.
  Malformed/authentication/unsupported-contract failures remain errors;
  startup, transient publication states and short transport timeouts remain
  cancellable waits under the caller's overall lifetime.
- A first feed implementation performed an unnecessary full point read before
  every subscription. Actual CLI startup controls caught its latency regression.
  Removed that warm-path read; only confirmed missing/dead-owner recovery uses
  the waiting point read. No startup or test deadline was increased.
- The final native ARM64 contained client/feed packet passes 140 cases in
  27.49 seconds, including actual CLI startup/update/cutover controls, delayed
  authenticated first publication, no premature output and real caller-timeout
  cleanup. The earlier x64 cache encountered the separately tracked #7643
  executable-retention problem; preserve it rather than changing ACLs, services
  or process custody. A separate runner-owned cache supplies the final packet.
- The existing manual Git/roster Linux and Windows portfolio now selects the
  same two files with unchanged budgets. Source publication, external
  qualification, release and authorized installed proof remain separate gates.
- Review identified two remaining admission gaps: terminal point-read errors
  need the normal versioned JSON envelope, and an initially stale complete feed
  transaction must not count as its first usable publication. Corrected both
  and retained stale continuity only after usable output. The updated
  contained native packet passes 145 cases with ordinary cleanup; no latency,
  transport or cancellation budget changed. Repeat exact-head external and
  source-review gates for this revision rather than reuse the prior result.
- Subsequent review found that a missing control-token file followed the
  absent-endpoint retry path indefinitely. Missing routes already report a
  connection error, so the waiting reader now lets the credential-file failure
  reach the existing JSON error boundary without restarting the live owner.
  The bounded internal reader remains unchanged. Actual CLI credential-file
  controls cover JSON, one-shot stream and follow output without synthesizing
  a credential or emitting successful rows. The contained native client/feed
  packet passes 148 cases with ordinary cleanup; source review, exact-head
  external qualification and installed acceptance remain pending.
- Documentation-impact review: the existing CLI and engine contracts already
  distinguish retryable startup from terminal authentication failures. This
  correction restores that documented boundary; no new option, protocol or
  reader contract is introduced.

### 2026-11-05 - Blocking reader source merged

- #10562 merged as `ff44a10389f0b735a8ead7ad41e34e3ceb95047d`.
  Copilot's exact-head review at
  `3e225109c0e76f8b84bfc95f965f02a372310e1f` reports no open issues.
  Its outstanding request for native evidence is satisfied by completed
  portfolio `41977196082`: both Linux and Windows jobs select the actual
  client/feed packet, including caller-timeout cleanup, and pass.
- The final metadata-triggered required CI run `41977742135` passes before
  the managed merge. All review threads are resolved. No test, job or
  transport deadline was widened.
- Re-read the parent and mini-effort after merge. No installed, updater,
  launch, singleton or handoff gate closes from this source result. Observe
  actual promotion and the separately authorized normal updater, then prove
  the installed reader before returning to the saved parent qualification.

### 2026-11-05 - Released reader; installed adoption still blocked

- Promotion `41981509607` succeeds. Main's release receipt names Dev snapshot
  `594895e3672637b3347dd645d960f24b070a7cb5`; its merge base with #10562's
  merge is that exact merge. This proves release inclusion independently of
  the workflow controller's own head commit.
- The full Dev checker passes on a successor with unchanged executable inputs.
  An earlier six-minute checker timeout remains recorded on #9807; the
  successful control does not claim a source fix for timing capacity.
- The selected `1.24.473-dev1` consumer answers a normal JSON point read with
  current quality, but actual loaded-module inspection shows no waiting-reader
  implementation. A proposed isolated cold check therefore stops at its
  source-identity assertion before creating a fixture. Warm availability and
  package numbers do not establish installed adoption.
- An explicitly approved release of one interrupted queue guard preserves its
  failed history and does not replay it. A fresh normal target update is
  accepted, but an older queued native provision first rejects changed
  admission inputs and leaves a different unsettled fence. The target remains
  pending; the prior approval is not blanket authority for further releases.
  No installed source is patched or retained service stopped.
- Preserve the saved parent continuation and all installed, whole-update,
  launch, singleton and handoff gates while reconciling the exact queue owner
  and required recovery scope.

### 2026-11-05 - Installed blocking-reader prerequisite qualified

- The existing normal updater adopts and serves `1.24.475-dev1` while its
  broader operation continues. Loaded client and feed modules exactly match
  released source blobs `26bab9d85f2da1374f930a6e02888a94aace91f8` and
  `6dacba43e1f199e29e1446993ffaf24573156690`. Authenticated health confirms
  the same selected serving slot and ready publication.
- An actual catalog CLI invocation returns current JSON, one selected row
  and no availability warning in 13.215 seconds. Its publication generation
  matches the authenticated resident health. This is measured invocation
  latency, not an overall CLI deadline or a performance guarantee.
- Isolated checks execute the installed CLI handler against a real
  authenticated test endpoint, substituting only fixture-owned startup and
  routing inputs. JSON and one-shot stream wait 1.059 and 1.140 seconds for
  delayed publication, emit nothing early and start exactly one private
  owner. Missing-credential construction returns a terminal JSON error
  without creating a token or restarting an owner. All fixture threads and
  endpoints close; no production daemon is stopped.
- A separate installed-interpreter process demonstrably enters the unready
  handler, emits no stdout and remains waiting until its real four-second
  caller-owned timeout. The canonical process owner settles that scope before
  returning the timeout. The fixture uses the existing JSON capture helper:
  ordinary stdout redirection alone does not capture the CLI's real-output
  boundary and supplied no valid output evidence in an initial probe.
- These controls close the blocking-reader prerequisite only and restore the
  saved Manager-reader/installed-set continuation. They do not prove a
  production-daemon cold restart, whole-update success, launch parity,
  singleton shape or handoff completion.
- The broader update is still running after a CodeSpaces dependency-cache
  lock timeout; component progress is not whole-set completion. The separate
  target request remains queued. An additional request falls outside the
  explicitly bounded queue-recovery pass, so further guard repair stops;
  the active installer and pending requests are preserved.

### 2026-11-05 - Installed native Manager reader qualified

- The original Manager-only request completed successfully; no duplicate is
  submitted. Existing normal updates now select `0.6.291-dev1`. Its loaded
  reader and native-observation modules exactly match #10514's reviewed
  source blobs `c91408541ff41541de8038b041ef41a939f09379` and
  `15ab8c229adede13cb37f9ebfaebb2492bcff228`.
- Two actual installed reads with a 35-second pause both verify the active
  selected daemon as ready, at the selected version and with the same native
  process birth identity. They take 3.359 and 3.578 seconds, with subprocess
  execution forbidden throughout the reads and zero attempted probes. No
  daemon is drained, stopped, replaced or otherwise mutated by the checks.
- This resolves the original diagnostic timeout/ownership-reader prerequisite
  and restores the original installed-set/full-update return. It is not a
  process-count or latency guarantee, a concurrent-launch singleton proof,
  or whole-update completion. The broader active installer, pending target
  request and all remaining parent Plan/Validation items remain open.

### 2026-11-05 - Requested target update completed; whole-set failures retained

- The original accepted Worktrees target request completes with exit zero
  and no error; its observer is stopped. The normal owner publishes and serves
  `1.24.477-dev1`, with selector, deploy manifest, source version and command
  lifecycle agreeing. Native cutover reports a healthy incoming daemon,
  committed routing and settled transition; outgoing retirement remains
  asynchronous and is not claimed complete.
- Loaded client/feed modules retain the exact already-qualified source
  identities. A fresh actual catalog JSON invocation exits zero in 4.592
  seconds with current data, no availability warning and a generation
  matching authenticated serving health. Unchanged delayed-publication and
  timeout controls are not rerun merely for a package-number change.
- The preceding whole update completes with exit one, not a fabricated
  success. Its five failed components are CodeSpaces and Containers
  (300-second UV-cache lock waits), Dispatch (published slot but cutover
  deadline), Vault (access denied replacing its version selector), and Budget
  Guidance (incomplete-slot cleanup refusal).
- The Vault selector is not read-only and its current user has FullControl;
  that metadata does not prove which access/sharing condition caused the
  earlier replacement failure. The failed-slot helper's Defender suggestion
  likewise is not a measured cause. No lock is deleted, permissions changed,
  timeout widened, optional service activated or protected process stopped.
- Another independently admitted automatic update is already running. Observe
  its real outcome without adopting its authority or claiming it resolves this
  effort's whole-set gate. The bounded repair pass remains stopped, the
  original broader failure remains retained, and launch, singleton and handoff
  validation are still unfinished.

### 2026-11-05 - Independent update completed; own incoming set still incomplete

- The independently admitted automatic update completes with exit zero and
  no request error. Its observer is stopped immediately. That operation
  explicitly excludes CodeSpaces, Containers, Index and Vault under its own
  machine policy; its success is not acceptance of this effort's requested set.
- This effort's actual inventory still selects CodeSpaces `0.5.254-dev1`,
  Containers `0.2.202-dev1` and Vault `0.1.212-dev1`, behind the candidates
  from the failed whole update. No skipped component is relabeled successful.
  The historical UV-cache lock owner and Vault selector sharing failure
  remain unproved; neither timeout tuning nor speculative permission changes
  supply the missing evidence.
- Dispatch now selects and serves `0.18.268-dev1`; Budget Guidance selects
  `0.1.131-dev1`, with the independent installer reporting activation and
  import verification. Those newer incoming observations supersede the
  corresponding failed candidates, not their failure history. They do not
  establish shared-daemon singleton shape or historical outgoing cleanup.
- Worktrees remains selected and serving on the qualified `1.24.477-dev1`.
  Manager now selects `0.6.293-dev1`; the prior reader qualification remains
  evidence for `0.6.291-dev1`, not an unmeasured claim about that newer slot.
- No new update, guard release, failed-request replay, process termination,
  lock removal or optional activation is performed. The bounded recovery pass
  stays ended. Preserve the installed-set continuation and later launch,
  handoff and measured-completion siblings while resolving the remaining
  normal-update authority and acceptance boundary.
- A newly approved single normal update stops at preflight, before submission:
  another native MCP request is already interrupted with `ParticipantError`
  and settlement required. Its log records `Native installation changed after
  driver admission`. A packaged-driver read confirms a saved/current source
  mismatch and the same addressed MCP root. The exact changed file and
  historical settlement remain unknown; no guard is released.
- Sent this distinct pre-installer refusal evidence to #7758's existing
  ordinary recovery/coalescing owner in
  [comment 6666242788](https://github.com/ThomasMichon/copilot-extensions/issues/7758#issuecomment-6666242788).
  Preserve #10499's MCP bootstrap-adoption and #10559's whole-bootstrap
  retention ownership rather than open a competing implementation. The
  approved submission is not consumed or silently renewed; the encountered
  guard is the explicit stop boundary. Whole-set acceptance remains blocked.

### 2026-11-06 - Post-restart incoming observations and routed-port reader

- Another owner released the interrupted MCP guard through operator repair;
  its failure and unconfirmed historical settlement remain unchanged. No
  release or new update is performed by this driver. Current runtime payloads
  and selections now agree, including CodeSpaces, Containers and Vault.
- The latest broader update still exits one: Vault selects `0.1.219-dev1`
  but fails its selected-service readiness check. A subsequent existing
  endpoint responds to ping without version or generation evidence. Preserve
  that service's separate ownership; a successful ping or selected package
  is not incoming-generation acceptance or permission to replace it.
- Manager selects `0.6.307-dev1`. Its installed native reader reports the
  actual resident as unknown because it has no explicit port argument,
  although the active routing record carries its port and process birth.
  The diagnostic correction uses only a loopback route with the same PID
  and a nonempty birth matching the observed process. Explicit command-line
  ports retain precedence; owner, root and post-health revalidation remain.
- Claimed this bounded continuation on #5579 in
  [comment 6678791116](https://github.com/ThomasMichon/copilot-extensions/issues/5579#issuecomment-6678791116).
  The contained reader, CLI and native packet passes 40 cases, including
  routed-port acceptance, post-health replacement and stale/missing/foreign
  route refusals. Required Python lint and the 13-plugin install contract pass.
- A read-only corrected-source consumer observes the actual selected daemon
  ready and active on `0.6.307-dev1` in 0.113 seconds, with subprocess probes
  forbidden and zero attempts. This changes no installed file or service and
  is not a latency guarantee or installed correction. Source review, release,
  actual installed reader and all parent whole-update/launch/handoff gates
  remain open.
- The actual Manager JSON CLI consumer also returns ready current-generation
  data with the corrected source reader and subprocess probes forbidden.
  Documentation consistency and effort structure pass. A separate optional
  whole-Manager MyPy probe stops on duplicate `conftest` modules before
  checking code; the project is explicitly pending, not an admitted strict
  scope. Preserve that migration limitation rather than claim type acceptance
  or alter unrelated test discovery in this reader fix.
- The first hosted native portfolio passes Linux and 137 Windows cases, but
  fails the Companion fixture when immediately recreating its session after
  confirmed CLI-session disappearance. The reader controls pass; the whole
  portfolio is not credited. No timeout or failed assertion is removed.
- The local default backend is PSMux `3.3.8`, not that portfolio's pinned
  `3.4.0`; its preparation timeout and later command timeout are separate
  observations, not reproduction of the hosted recreation failure. The exact
  pinned archive verifies against the workflow digest, and the original
  native fixture passes locally with that backend in 25.73 seconds.
- Make this fixture's cold-server profile explicit with `PSMUX_NO_WARM=1`,
  matching the sibling actual-Companion fixture. PSMux documents its default
  warm-session pool as standby-server registry renaming and claim messaging;
  this lifecycle fixture must not depend on that independent reuse path.
  Real pane, CLI identity, observer exit, same-name recreation and incarnation
  assertions remain, with the original subprocess and hosted test deadlines.
  The historical failure's exact warm-pool condition is not proven, and no
  production warm-pool correction or host update is claimed. Fresh hosted
  qualification of this changed fixture remains required.
- The changed local packet passes all 41 assertions, then its canonical group
  cleanup refuses deletion of the copied x64 fixture interpreter with Windows
  access denied. That invocation remains failed, not green. #7643 tracks this
  Windows ARM64 executable-retention boundary; this observation does not
  identify the retaining process. No protected process, lock or artifact is
  forcibly cleared. Qualify the same packet with the existing native ARM64
  interpreter in a separate runner-owned cache, preserving the refused scope
  and every containment, test and cleanup limit.
- The native ARM64 variant reaches the real pane/navigation flow but hits the
  unchanged 120-second item bound while spawning a later mux query. It is not
  a passed native packet or a reason to widen the limit. Keep both local
  environmental failures and the hosted failure in the evidence; complete
  fresh hosted Windows qualification before merging the fixture correction.

### 2026-11-06 - Routed-port source merged; parent return retained

- #10916 merges as `88f51e11612171638d3ae9667534efca6e5ac56a` after
  current-head review `5797030423` reports no open issues and its requested
  exact-head native evidence is supplied. The earlier dismissed approval is
  not used. Review threads are empty; required CI `42181133437` succeeds
  before the normal role-scoped merge. No CI or thread gate is bypassed.
- Native run `42180608803` succeeds on the exact published head
  `6f79db48d8f11c6faf5081d4fc0253caa4a8b0ee`. Linux and Windows jobs pass.
  The Windows Companion/reader packet passes 138 cases in 65.75 seconds
  with successful ordinary cleanup; its bounded owner-lookup packet also
  passes. All earlier failed runs and cleanup/timing observations remain.
- Re-read the canonical parent and agenda after merge. The source correction
  is complete, not installed adoption or whole-update acceptance. Preserve
  the separately owned serving-generation gap, the original installed-set
  return and every later launch, handoff and measured-completion item.
- The ordinary pull-forward helper aborts a squash-history replay conflict.
  Verify all owned code, tests, changefile and effort bytes against the merged
  revision; the owned README section also agrees, with only an unrelated
  upstream addition elsewhere. Retain a local backup and advance the already
  merged history without replaying or discarding unmerged work.
- Observe containing release and current installed consumer through their
  normal owners before claiming the corrected reader live. No new update,
  Vault intervention, retained-session migration or worktree finalization is
  performed by this source result.
