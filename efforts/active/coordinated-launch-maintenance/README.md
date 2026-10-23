# Coordinated Launch Maintenance

- **Slug:** `coordinated-launch-maintenance`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-slice PRs against `dev`
- **Created:** 2026-10-21
- **Status:** Active
- **Vision:** `visions/installer/README.md` (adopted out-of-band automatic maintenance); `visions/picker/README.md` (responsive session launch)
- **Umbrella issue:** #7694

## Guiding Intent

An already-healthy session must not wait for unrelated runtime installation.
Keep adopted automatic maintenance available, coordinated and observable rather
than disabling updates or starting an independent updater for every launch.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launch driver | Design, implementation, review, release and qualified acceptance | Managed source `welded-circuit`; #7694 |
| Broker repair | Follow-up #6859 bug and native qualification prerequisite | Managed source `whirring-doohickey`; same launch driver |
| Broker design | Source-only lifecycle amendment and review | Managed source `tinkling-cylinder`; same launch driver |

## Coordination

- **Topology:** sequential source PRs against `dev`.
- **Host (owns PRs):** Launch driver.
- **Delegates:** none.
- **Handoff:** canonical effort and #7694; no silent ownership transfer.
- #6812 retains formatting, telemetry and operation-cost audit ownership.
- #6820 retains historical-command migration and its paused constructor handshake.
- #7667 retains its contributor-owned installer/context implementation.
- #6237/#3336 retain release control. This effort does not authorize retries,
  installed mutation, service restarts or another concurrent updater.

## Context

The launch scripts stage a marketplace download but join it before handoff,
then run bootstrap maintenance and two plugin-reconcile passes synchronously.
Only one core installer branch is detached. Several healthy launches can
therefore wait behind slow installation; a detached child alone does not prove
coalescence, distinct-project delivery or survival of caller Job cleanup.

This closes the installer's existing adopted, out-of-band maintenance intent,
using the process-lifetime and operation-custody patterns. The original
no-new-service mechanism proved insufficient for the Windows lifetime guarantee.
The source-only broker amendment below supersedes that mechanism without
changing dependency policy or authorizing installed mutation.

**Current delivery:** the operator selected scheduling-only completion of
#7788. It submits optional work through the already-merged canonical execution
driver and does not change daemon admission, add a broker or introduce another
mutation owner. The canonical Windows implementation refuses a bootstrap or
resident still in any Job. A rejected worker must remain a visible durable
failure, never a successful acceptance or foreground-installer fallback; the
launcher can still hand off on its healthy retained runtime.

The same driver retains independent ownership of #6859 and draft #7833.
#6918 is merged, and neither its historical author nor another PR's delivery
is a dependency of #7788. The immediate-Job inference and new Scheduled Task
design belong to that separate repair, not the scheduling diff. Its reviewed
contract, failed candidates, native qualification, generation retention and
installer integration remain open there without a lifetime-check waiver or
deployment authorization. Historical journal entries below record the former
broader hold; the explicit scheduling-only decision supersedes that delivery
coupling, not the independent repair's technical gates.

## Request

Public-safe literal excerpt: "Installation is just dragging out and blocking
other worktree starts".

Settled direction (summary): use coordinated-background automatic maintenance,
not permanent update opt-outs; preserve the running installation and do not
start another updater as an investigative shortcut.

Confirmed effort slug: `coordinated-launch-maintenance`.

Subsequent operator direction (verbatim):

> 6918 is merged. 6859 is a bug; if we need it fixed, we should fix it

Design-scope form selection (verbatim): `source-redesign`.
The selected option was "Authorize the source-level broker redesign; keep
deployment separately gated". This authorizes source design and implementation,
not installation, task/service registration, a host update or live rollout.

Launch-overlap form selection (verbatim): `reuse-merged-driver`.
The selected option was "Rework our draft around the merged execution driver
(recommended)". Reuse #7860's canonical execution front door and retain only the
remaining off-handoff scheduling work plus the independent #6859 broker repair.
This does not expand deployment authority.

Native-probe form selection (verbatim): `disposable-windows-ci`.
The selected option was "Allow a disposable Windows CI experiment, including
synthetic task registration and cleanup". This permits only a credential-free
probe on an ephemeral Windows CI runner. It does not permit task registration
on shared hosts, host updates or deployment.

Merge-scope form selection (verbatim): `scheduling-only`.
The selected option was "Qualify and merge the scheduling change; keep the
broker redesign separately tracked and owned". This retains the canonical
execution driver's lifetime refusals and does not authorize installed rollout.

## Plan

### Phase 1 - Reviewed contract
- [x] Land this plan through ordinary review before implementation.
- [x] Coordinate the bounded scope on #6812/#6820 without taking over their owners.

### Phase 2 - Coordinated maintenance

The following implementation choices are **agent-recommended** mechanisms for
the requested coordinated-background behavior.

- [x] Review the [independent Windows broker amendment](windows-launch-broker.md)
  before implementing its lifecycle and consumer integration. No deployment is
  implied.
- [x] Transfer the independent broker repair, its installer integration and
  lifetime qualification to separately owned #6859/#7833 under the reviewed
  [broker contract](windows-launch-broker.md). They are not dependencies of the
  scheduling-only #7788; no repair or qualification is claimed complete.
- [x] Move optional stage/bootstrap/reconcile work off the healthy handoff path
  on both PowerShell and Bash; remove the join and inline-download fallback
  from ordinary launches. Preserve explicit blocking recovery behavior.
- [ ] Use one bounded on-demand owner and durable pending requests. Coalesce
  equivalent requests without losing a different project, repository, source,
  installation context or stronger requested operation.
- [x] Share operation custody with explicit update/reconcile entrypoints;
  retain canonical installer locks and execution admission. A busy owner is
  pending work, not permission for another installer.
- [ ] Preserve exact action argv, scoped environment/unsets and project
  context. Re-plan against the current installed state rather than execute a
  stale or foreign action. Keep runtime replacement and retention safety.
- [ ] Use the canonical headless lifetime primitive, explicit logs and durable
  working directory. No inherited-Job fallback presented as durable success;
  preserve contained-test suppression and bounded child-tree cleanup.
- [ ] Make queued/running/failed/completed outcomes inspectable and truthful.
  Interrupted work must not be reported complete or retried over a possibly
  surviving child without ownership/completion evidence.
- [x] Preserve `WORKTREE_NO_UPDATE` and `WORKTREE_NO_RECONCILE` independently,
  explicit update behavior, clean JSON/stdout and required first-use safety.
  Missing prerequisites cannot be called a healthy launch merely to avoid waiting.

### Phase 3 - Release and acceptance
- [ ] Land source, documentation and changefiles through review and required CI.
- [ ] Observe legitimate promotion, then use the authorized normal rollout
  owner; do not start a competing update to finish this effort.
- [ ] Record installed handoff, maintenance completion and failure evidence.
  Close this effort only when every validation item is resolved or explicitly
  transferred to a named tracked objective.

## Validation Plan

All detailed scenarios below are **agent-recommended** acceptance checks.

- [ ] Unit tests cover queue equivalence, distinct contexts, stronger requests,
  owner contention, crash/ambiguous-child handling and truthful terminal states.
- [ ] Real shell fixture holds an installer behind a gate while two healthy
  launches reach handoff before that gate opens; maintenance remains automatic
  and single-owned. Measure handoff independently of installer completion.
- [ ] Prove a different project's pending request is eventually processed,
  and manual update/reconcile cannot overlap its mutation boundary.
- [ ] Exercise exact argv/environment, admission refusal, malformed plans,
  failed spawn, failed installer, opt-outs and explicit blocking mode.
- [ ] Prove first-use/missing-prerequisite handling in an isolated fresh home.
- [x] Transfer Windows independent broker activation, all-caller-Job survival,
  native control/desktop observation and installer lifecycle qualification to
  #6859/#7833. The scheduling change retains existing strict admission; verify
  that its refusal produces no accepted receipt or foreground fallback.
- [ ] Exercise native POSIX handoff, owner exit and pending-request delivery.
- [ ] Run targeted contained suites, lint, install-contract and source guards;
  record native/live lanes separately from mocked wiring.
- [ ] Verify a promoted installed release through the normal authorized flow;
  a payload version or queued receipt alone is not installed/running proof.

## Proposal

The scheduling change adds no service, periodic updater or maintenance queue.
The independently authorized Windows broker redesign remains a separate
tracked objective. The launcher submits optional
work to its installed owner's bounded coordinated maintenance path and proceeds
on the healthy retained runtime. The owner drains admitted requests using the
existing planners/installers and then exits. Coordination must cover explicit
mutation entrypoints as well as launch requests; a lock around only the new
worker is insufficient. Machine-local request/status/log data never enters
repository history.

## Journal

### 2026-10-21 - Kickoff

- Traced foreground stage join, bootstrap and two-pass reconciliation on both
  shells. Existing progress work did not create the serial maintenance path.
- Claimed #7694 and created isolated source `welded-circuit`; no installed
  updater, running launch, service or registry was changed.
- Confirmed the effort slug. Compared the captured request and settled
  direction with the operator input; implementation/test mechanisms are
  explicitly marked agent-recommended.

### 2026-10-21 - Reviewed contract

- #7716 merged after exact-head approving review and successful required CI.
  Synced the managed source onto the reviewed plan before implementation.
- Posted the bounded ownership notice on #6812 and #6820. Their retained
  scopes and #7667's contributor-owned implementation remain unchanged.
- The initially assigned backend worker did not survive the session
  interruption and left no source changes; the launch driver retains and
  directly resumes that implementation. No production maintenance was started.

### 2026-10-21 - Source implementation and qualification blocker

- Implemented durable optional requests, equivalent-request coalescence,
  distinct project/selector delivery and operation-scoped mutation custody.
  Different inherited feed/auth/package-policy environments retain separate
  workers; only their digests enter the queue. Scope workers wait for shared
  custody and release their empty-queue ownership transactionally.
- Removed the shell stage join, inline-download retry and shell-owned
  detached installer. Both shells submit through the same engine owner;
  blocking recovery also uses that custody. Explicit update, marketplace
  stage and applied reconciliation share the mutation boundary.
- Preserved canonical planner/installer admission, action environment/unsets
  and installer deadlines. Nonzero acquisition, diagnostic-only refusal and
  failed actions cannot produce successful completion. An interrupted mutation
  remains blocked without a positive completion witness.
- Targeted contained backend and native shell contracts passed. The real
  process fixture proves two handoffs before a gated installer completes,
  single mutation ownership, eventual distinct-project delivery and separate
  inherited-environment delivery. Existing apply-plan consumers passed.
  Two unrelated smoke fixtures initially omitted required installer siblings;
  repaired only their fixture assembly and confirmed the native smoke paths.
- Linux dependency preparation did not finish within its unchanged bound;
  the new test environment had no pytest or runtime packages. The stdlib-only
  native fixture then ran successfully under the existing bounded supervisor
  from a disposable native-filesystem source snapshot: both real process
  contracts passed. This does not qualify the unfinished full dependency graph.
- Windows caller-Job qualification exposed and corrected a local integration
  issue: use canonical `direct_python` plus its venv environment before
  `spawn_daemon`, rather than hand the broker a GUI interpreter or redirector.
- The strict no-breakaway caller still fails at shared broker
  `bootstrap-identity`. Bounded diagnostic instrumentation proves PID and
  cookie match, but `_outside_job(bootstrap)` is false. The shared primitive
  refuses startup with confirmed cleanup; no inherited-lifetime fallback,
  policy weakening or host remedy was attempted. #6859 retains this
  prerequisite's original owner. The effort remains Blocked, not Done.
- Reported the failed predicate to #6859's retained owner. Native fixture
  cleanup now waits on each observed worker's exit, not merely the last
  database completion row; that closes a real Windows log-handle cleanup race.
- Final contained backend execution passed with the production-survival lane
  explicitly suppressed under containment. Native Linux Python fixtures and
  eight native Bash handoff subcases passed from disposable native-filesystem
  snapshots using the existing bounded supervisor and standard-library runners.
  Native Windows handoff exercised sixteen subcases with the production log
  helpers. One combined progress group encountered an unexplained twenty-second
  formatter pipe-completion timeout; both exact formatter nodes passed in
  isolation at the unchanged budget. This is not an accepted flake exemption
  or a claim that the entire combined group passed.
- Reconciled the moving Dev base with a backup ref, preserving #7727's Bash
  fallback diagnostics and #7736's marker normalization plus retained formatter
  tests. The exact rebased Windows group then passed: nineteen tests and
  sixteen handoff subcases, with seven native-POSIX skips. Native Bash's eight
  subcases also passed against the rebased source. No deadline was raised or
  failed check bypassed. The shared broker blob is unchanged; #6859 remains
  the substantive native acceptance blocker.

### 2026-10-21 - Own the follow-up bug

- Captured and read back the operator's explicit direction above: merged
  implementation #6918 does not create a standing ownership hold on bug #6859.
  No open broker PR was found. Claimed the follow-up under source
  `whirring-doohickey` rather than waiting for an unverified active owner.
- Native observation found ambient Jobs distinct from the caller's cleanup
  scope. Merely adding breakaway flags did not fix the failure; those
  speculative flag changes were removed. The candidate instead queries complete
  caller-Job membership and retains both pre-staging bootstrap and pre-resume
  resident checks, identity admission and fail-closed query handling.
- Focused protocol/error tests passed, and the actual containing consumer
  completed two strict caller-Job cleanup cycles with the candidate library.
  Shared-consumer validation, review and publication still remain open.

### 2026-10-22 - Review exposes the ancestor-Job proof boundary

- Published the broker candidate for review without installing or activating it.
  The original contained shared suite passed 416 tests; the separate production
  Windows lane passed eight tests, and the real maintenance consumer completed
  two cycles after closing both nested caller Jobs.
- Pulled the repair forward after #7811 merged. Retained its canonical process
  library consolidation rather than restoring the removed Worktrees vendor.
  The composed shared suite passed 452 tests with 30 platform/lane skips.
  Two runs encountered fixture-image deletion errors after passing assertions;
  exact retained fixture roots were subsequently retired without killing
  unrelated processes. A focused native run and an unchanged full canonical
  runner then passed including sandbox retirement. No failure waiver was used.
- Review found that the passing nested-Job fixture proves the exercised
  topology, not every ancestor-sharing topology. Windows documents that
  `QueryInformationJobObject(NULL)` queries only the immediate Job. A candidate
  sharing an outer cleanup Job but not the caller's inner Job could pass the
  candidate check and still die when the outer Job closes.
- Held the repair as a draft and kept this effort blocked rather than weaken
  the lifetime contract. An independently managed Windows launch broker would
  change the no-new-service design and requires operator direction. The other
  review findings remain owned with the same repair, not waived.
- The merged execution driver under #7758 now owns installation execution.
  The launch slice must integrate with that canonical owner instead of retain
  its own competing mutation custody. Posted coordination on the driver change;
  no rollout or additional updater was requested.

### 2026-10-22 - Source-only broker redesign authorized

- Asked for a design-scope decision rather than infer service authority from the
  original bug-fix request. The operator selected `source-redesign`, approving
  the source-level independent broker option with deployment separately gated.
  Read the selection back against the offered option and preserved that limit.
- Resume design work: reconcile the lifecycle patterns and standing service
  vision, then review the amended contract before implementation. All broker
  topology, lifecycle and transport choices remain agent-recommended mechanisms.
- Do not register or start a host task/service, install a candidate, restart
  shared services, or launch a competing updater under this authorization.

### 2026-10-22 - Broker design review complete

- #7910 merged as `8b1437d07f9808a48009a2683d7bff394fd70e56` after an
  exact-head Copilot approval and successful required CI. The managed merge
  command timed out after the provider accepted it; a separate scoped provider
  read confirmed `MERGED`, rather than blindly retrying the mutation.
- Re-read this effort's Plan and Validation Plan. Only the design-review
  prerequisite is closed. Actual broker implementation, independent native
  activation/transfer proof, consumer integration, release and authorized
  rollout remain open; a reviewed plan is not an implemented broker.
- The repair source now retains retryable query-Job/member-handle custody,
  checks the deadline within its census, and isolates synthetic PIDs. The full
  composed shared suite passed 455 tests with 30 platform/lane skips, including
  canonical sandbox retirement. These corrections do not resolve the remaining
  ancestor-Job proof boundary by themselves.
- Start the broker implementation from the reviewed contract, preserving the
  source-only boundary. The plan-only source's pull-forward helper refused a
  deleted PR ref; its merged provider state is confirmed and its checkout is
  retained, not manually reset or removed.

### 2026-10-22 - Reconcile the merged launch execution front door

- Pulled the independent repair forward, preserving #7860's newly merged
  caller-Job guard and cancellation handling. The two owned repair commits
  are now based on current Dev; the independent broker is not implemented.
- #7860 also routes native launch bootstrap, reconciliation and staged apply
  through the canonical execution driver. Its launchers still join optional
  preparation before session handoff, so the responsive-launch objective is
  not closed. However, this effort's draft #7788 now overlaps a merged
  implementation and retains competing maintenance custody.
- Pause that substantive integration choice for operator direction rather
  than silently supersede the draft or restore its separate execution owner.
  The recommended continuation is to reuse the merged execution front door
  and narrow this effort's implementation to off-handoff scheduling plus the
  independent #6859 lifetime repair.
- The rebased shared assertions passed 467 tests with 30 skips. The outer
  sandbox retirement failed on an installed fixture image; all 90 recorded
  process birth identities were subsequently confirmed exited and the exact
  retained sandbox was removed. This is not a green full-runner result or a
  failure waiver. The focused lint check passed.
- Task Scheduler's documented engine PID is not an action-process identity
  guarantee. Do not assume equality or use task registration alone as the
  independent activation proof. Native activation and lifecycle qualification
  still require an approved disposable Windows lane; this work has registered
  no task, changed no installation and restarted no service.
- The operator selected `reuse-merged-driver`. Continue by reworking this
  effort's own draft around #7860, not by retaining a second execution owner
  or declaring the remaining responsive-launch and broker gates complete.

### 2026-10-22 - Reuse the execution owner for automatic maintenance

- Preserved the original draft and qualification work in a local backup before
  reworking it around merged #7860. The replacement has no separate maintenance
  queue, worker implementation or mutation lock.
- Added fixed `launch-update automatic` execution inside the existing driver.
  Healthy native launchers submit it with `--detach`, then proceed without a
  shell-owned stage, installer join or inline-download fallback. Explicit
  blocking recovery keeps the existing fixed-phase path and progress handling.
- Acquisition now uses bounded caller-owned process control and records its
  real exit outcome. Automatic acquisition refusal, publication refusal and
  phase failure produce failed durable outcomes rather than installation
  success. Update and reconciliation opt-outs remain independent.
- The actual Windows shell, canonical request boundary, finite driver,
  bootstrap executor and a gated fixture installer proved four handoffs before
  the installer gate opened. Equivalent pending requests coalesced without
  losing another project's request, and both projects completed after release
  of the gate under the same execution owner.
- The final contained composed suite passed 140 tests with 15 explicit
  platform/lifetime skips, including canonical sandbox retirement. Installer
  contracts passed for all thirteen plugins. This is real handoff/queue proof,
  not caller-Job survival, a registered broker test or installed qualification.
  Native POSIX and the independent Windows broker lanes remain open.
- The operator separately authorized `disposable-windows-ci` for a synthetic
  Task Scheduler activation/cleanup experiment. Use that lane to determine
  actual instance/process binding and conditional child escape before treating
  the Scheduled Task candidate as supported. The shared-host prohibition and
  deployment gate remain unchanged.

### 2026-10-22 - Disposable native activation candidate works

- Ran the explicitly selected Windows probe in workflow run
  [39850345336](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39850345336)
  against exact repair head `7b4e857a3e292470f4e94739239fca279b751688`.
  Verified that the actual Windows probe step ran successfully; unrelated
  native modes were skipped, not credited as passing.
- Both independently activated task instances reported an engine PID equal
  to the native pipe-server PID. The broker's process generation was pinned
  and checked. Both brokers were themselves in a Job, but each suspended
  resident successfully broke away and was confirmed in no Job before resume.
- Each actual child resumed without a visible console and remained alive
  through two observations after its finite broker exited. Actual native
  child handles controlled termination and confirmed exit. The artifact reports
  both child cleanups and deletion of the uniquely owned S4U registration.
- This supports the finite, per-user, on-demand task candidate on the
  exercised runner. It does not establish a universal engine/action PID rule:
  production admission must verify the equality on each actual instance and
  refuse mismatches. It also does not qualify authenticated production IPC,
  nested caller-Job survival, installer adoption/update/rollback or a shared
  host rollout.
- Continue canonical broker implementation using these conditional native
  checks, not the unproved immediate-Job inference. The three other retirement
  corrections and the probe passed the unchanged full contained runner:
  476 tests passed, 30 explicit platform/lifetime skips, with sandbox retirement.

### 2026-10-22 - Implement finite native transaction before normal-launch adoption

- Added the finite transaction, actual native handle duplication and an explicit
  consumer-owned registration/lifecycle adapter in the held repair source.
  Reused the canonical private Windows transport rather than introduce another
  secrets-bearing channel. It is an optional library capability, not a sibling
  plugin dependency or an automatic installer.
- The client authenticates the scheduler instance and native pipe process
  before sending live launch data. It adopts actual child control before resume;
  the child must be outside every Job. The finite broker stays alive through
  final commit and client pipe closure. Failed retirement retains retryable
  custody and refuses more work rather than silently permitting a duplicate.
- Contained protocol, adoption, framing, custody and existing process regressions
  passed: 637 tests, 31 explicit platform/lifetime skips, including canonical
  sandbox retirement. Lint, native PowerShell parsing, thirteen installer
  contracts, source-vendoring checks, headless guard and trusted workflow policy
  passed. The workflow-policy check used the existing YAML-capable test
  environment after the process-only environment explicitly lacked that tool.
- Extended only the already-authorized disposable Windows probe lane to exercise
  the production protocol through synthetic registration, nested caller-Job
  closure, exact resident arguments/environment/logs and native cleanup twice.
  That lane has not yet run against this candidate, so these are not native
  qualification results.
- Normal launches still use the old fallback pending cohesive replacement.
  Installer health-gated adoption/update/rollback, scope propagation, actual
  caller-Job negative topology, full desktop visibility, native POSIX and the
  containing maintenance consumers remain open. This is not a completed #6859
  repair, a source merge, promotion or host deployment.

### 2026-10-22 - First-touch native qualification exposes task lookup mapping

- Exact-head run
  [39860195613](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39860195613)
  executed the real Windows step. Its preliminary capability probe passed, but
  the production-protocol fixture failed during first adoption; it completed no
  resident cycles and did not claim cleanup or qualification.
- A missing task can surface as `System.IO.FileNotFoundException`, rather than
  only a COM exception. Corrected that exact missing-object case without
  treating denied or unknown lookups as absence. The native Windows PowerShell
  regression executes the actual lookup function against synthetic CLR methods;
  missing and access-denied cases both passed without connecting to or modifying
  Task Scheduler. The composed adoption suite passed 24 tests.
- Published the correction on the held repair draft and dispatched only the
  authorized disposable lane again in
  [39862177885](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39862177885),
  exact source `186c0e3003231c7a22692f1bdbf36ae41ac5c5e1`.
  That result remains pending here; a retry dispatch is not a passing result.

### 2026-10-22 - Native adoption progresses; activation remains unqualified

- The second run rejected the registered principal's account-name form as if it
  were a different owner. Normalized account names and SID strings through the
  OS SID resolver, preserving exact principal ownership. Native PowerShell
  mapping and adoption tests passed 26 cases without host registration.
- Run
  [39863715312](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39863715312)
  at `e01073ba24267f95c57a02e72a97769e27d50709` passed first/repeat adoption,
  status and owned removal sufficiently to confirm cleanup. The fixture caller
  then failed before IPC because its case-sensitive synthetic environment filter
  dropped Windows' uppercased `SYSTEMROOT`. Corrected only the fixture allowlist;
  retained credential exclusion and added three casing controls.
- Run
  [39864605984](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39864605984)
  at `8deff8d33e968c2b85bf68203c0a0656f989eaac` reached actual activation from
  the nested caller, but the bounded scheduler adapter exceeded its unchanged
  thirty-second deadline before returning control. Owned cleanup was confirmed;
  no resident cycle or production pipe handoff passed. This is the current
  native blocker, not a successful lifetime result.
- Narrow read-only controls proved that the filtered caller environment can
  initialize the scheduler COM connection, read the exact adapter's JSON stdin
  and refuse an absent task under caller Job containment. No task was created or
  started on the shared host. Diagnose the actual activation wait with bounded,
  fixed diagnostics rather than increase the deadline, infer independent
  lifetime or proceed to normal-launch adoption.
- Added an unqualified negative-topology fixture in the repair worktree:
  distinct immediate Jobs under one shared outer Job must fail the canonical
  child offer, and the native control must die when the outer handle closes.
  Positive controls now close actual caller Job handles rather than use
  termination as a proxy. Source/admission tests passed 15 cases; the new
  topology has not yet run on the disposable Windows lane.

### 2026-10-22 - Shared-ancestor negative proof and bounded activation evidence

- Exact-head disposable run
  [39872678419](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39872678419)
  at `72f65f3ed78a78b162dbb34f577fce279470c0d6` established different
  immediate Jobs under one shared outer cleanup Job. The canonical native child
  offer refused durable success, and closing the actual outer Job handle killed
  the controls. This closes that negative-topology item only.
- The production activation still completed no resident cycles. The adapter
  returned a nonzero native result; the former timeout-only diagnostics did not
  preserve its last fixed phase. Cleanup was confirmed. Neither this result nor
  the preliminary capability probe qualifies the production handoff.
- Added bounded raw timeout evidence after confirmed retirement and reader
  joins, plus activation-only, fixed allowlisted phase diagnostics. Native
  errors never disclose request, task XML or arbitrary COM exception text.
  Activation deadlines remain unchanged. The composed contained regression
  suite passed 113 tests with three explicit platform skips; the refined
  nonzero-refusal adapter suite passed 35 tests. Lint and native parsing passed.
- Published the nonzero-refusal phase correction on the held repair draft and
  dispatched exact source `616d9e3f7dadada8cede035ede12040dcb29c427` in
  [39873481651](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39873481651).
  It timed out in the input-read/parsing stage before scheduler connection;
  cleanup and the negative topology passed again, but no resident cycle did.
- Added a native nested-live-parent stdin regression using a synthetic
  PowerShell script, without a scheduler connection. Script-file JSON input
  reached EOF locally while the outer caller's input pipe remained open. That
  narrows the hypothesis but does not explain the CI-only wait. The fixture and
  refined input subphase passed 36 contained cases.
- Published exact source `81f10c880565ad157008939767f687223ccf75f3` and
  dispatched
  [39874898967](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39874898967)
  to distinguish input EOF from JSON parsing in the actual disposable caller.
  That run reached `run-requested` and refused in the actual `RunEx` call;
  neither stdin nor scheduler connection explains that particular refusal.
- The documented native `RunEx` contract accepts a BSTR or BSTR array, not an
  untyped variant array. A regression evaluated the actual source expression
  against native VARIANT marshalling without connecting to Task Scheduler.
  It rejected the original object array. Explicit `string[]` marshalling then
  passed the native `VT_ARRAY | VT_BSTR` check and all 36 adapter cases.
- Published the argument-type correction at
  `5b749a980749ef41dc3934237792362ec3b41b0e` and dispatched
  [39875766662](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39875766662).
  Continue from that exact native result before normal-launch integration;
  no shared-host adoption or deployment is authorized.

### 2026-10-22 - Production protocol passes the disposable native lane

- Run
  [39875766662](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39875766662)
  still timed out before JSON parsing completed. The BSTR-array correction
  alone did not qualify activation. The synthetic caller had removed OS profile
  and program-directory context from its environment; restored only an explicit
  OS-variable allowlist, retaining arbitrary credential exclusion.
- Exact-head run
  [39876801692](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39876801692)
  at `4d75361610125ad1818fd867eda164c4db05630b` passed the actual Windows
  probe step and production protocol. Both resident cycles retained actual
  process control, survived closure of all fixture-owned caller Jobs and finite
  broker exit, delivered exact argv/environment/cwd and split logs, had no
  visible own console, and completed actual child retirement.
- The different-inner/shared-outer negative control passed again, and the
  result confirmed removal of the uniquely owned registration and owned native
  resources. This is synthetic source qualification, not installed deployment
  or complete desktop-focus evidence.
- The composed process, private framing/Windows transport and probe suite passed
  663 tests with 31 explicit skips and confirmed sandbox retirement. The
  diagnostic/context-focused suite passed 56 cases; lint passed.
- Continue with owning-installer health gating and lifecycle/rollback evidence,
  then cohesive default consumer integration. The ordinary launch path still
  retains the old fallback; this repair draft must not merge or deploy as a
  completed fix. Native POSIX, containing maintenance consumers, review,
  legitimate release and separately authorized rollout remain open.

### 2026-10-22 - Exact action health and native lifecycle pass

- Added a bounded exact-`pythonw` health entry before registration mutation.
  It imports the immutable action's colocated transport, checks native user
  identity and requires a strict readiness response. Missing dependencies and
  unhealthy candidates cannot change task registration or published metadata.
  The real copied-runtime health and targeted adapter/probe suite passed 62
  contained tests; lint passed.
- Exact-head run
  [39878472832](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39878472832)
  at `0f238d4772557db38e017a7fdfa738e8f6ee3442` passed the actual Windows
  step, both native resident cycles and the shared-ancestor negative control.
  It exercised first/repeat adoption, a replacement immutable action, unhealthy
  candidate refusal, restoration after injected publication failure, stop,
  readoption and final owned removal. Cleanup was confirmed.
- This closes the bounded native lifecycle scenario, not whole-installer
  integration. Reuse canonical installation ownership and crash-consistent
  publication/recovery before wiring normal launch: task registration and
  configuration publication are separate effects, and synchronous rollback
  tests do not prove interrupted-transition recovery. Scope propagation,
  generation retention, standalone dependency closure, desktop observation,
  native POSIX and actual maintenance consumers remain open. Both drafts stay
  held; no deployment authorization has changed.

### 2026-10-22 - Publication intent implemented; changed candidate is unqualified

- Source `97d9820907281f23dd367cfda3d8aab668d1ad47` adds installer-owned
  credential-free publication intent before Scheduler mutation. Ordinary
  admission refuses while recovery is pending. Explicit recovery restores the
  original action, enabled state and configuration, preserves prior adoption
  on repeat-install failure, revalidates metadata around native effects and
  retains unresolved intent. It introduces no second execution lock, queue,
  automatic replay or normal-installer adoption. Both generations still need
  canonical retention; power-loss atomicity is not claimed.
- The changed broker/protocol/probe suite passed 127 contained cases and
  retired its sandbox; lint passed. The wider process run did not qualify:
  three native cases reported unconfirmed process exit or a member appearing
  after census. Those three passed in isolation; a larger 167-pass run still
  failed deletion of its installed-console fixture executable. Neither result
  is a full-suite pass or a waiver. These failures remain owned prerequisites.
- Exact-head disposable run
  [39886572524](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39886572524)
  failed the production protocol before its first custody transfer:
  activation timed out at fixed phase `input-read`. The shared-ancestor
  negative control and final owned cleanup passed. The new real
  subprocess-death controls, placed after the two publication boundaries,
  were not reached and are **not qualified**. The phase narrows the failing
  interval; it does not establish why conversion/configuration selection
  stalled. No unchanged retry or timeout increase is justified.
- Keep both drafts held and the effort Active. The next action is bounded
  evidence for this actual nested-caller activation stall and the native
  retirement/fixture-cleanup failures, before installer integration or default
  replacement. Historical native passes qualify only their recorded source.
  No shared-host registration, update, restart or rollout occurred.

### 2026-10-22 - Drive scheduling-only delivery through review

- The operator explicitly selected `scheduling-only`: qualify and merge #7788
  using the existing execution driver, retaining #6859/#7833 as a separately
  owned repair. The current target already refuses a broker bootstrap or
  resident in any Job; #7788 does not change that admission code or install a
  Scheduled Task. This supersedes the prior containing-PR hold, not a lifetime
  check or the independent broker's remaining qualification gates.
- Rebased the owned scheduling branch onto current `dev`, retaining peer
  effort-index additions and the unrelated native post-exit diagnostic fix.
  A local backup preserves the previous published tip. No other contributor's
  open PR is adopted as a delivery dependency.
- Added a real canonical-driver refusal contract: rejected worker startup
  returns no accepted receipt, retains a pending request and durable error,
  preserves strict `contained_fallback=False` admission and never executes an
  installer in the foreground. Native launcher controls also demonstrate
  handoff after that refusal and preserve logged failure details.
- The composed Windows scheduling slice passed 163 cases with 20 explicit
  platform/lifetime skips and confirmed sandbox retirement. The unchanged
  driver's equivalence, stronger-request identity, custody, contention and
  failure contracts passed 34 cases with one native capability skip. Install
  contract and touched-source lint passed. An earlier run reported machine
  environment drift and is not counted as passing; no host configuration was
  rolled back.
- Added a manual targeted mode to the existing hardened Linux integration
  route for Worktrees and Manager. It retains exact candidate pinning,
  dependency admission and network-isolated execution, records the actual
  selector/outcome, and cannot file the full-portfolio failure tracker.
  Scheduled nine-plugin coverage and disabled Windows rotation are unchanged.
  Native Linux execution, final review and required PR CI remain pending.

### 2026-10-22 - Linux qualification finds the native endpoint length limit

- #7788 is reviewable. Its first targeted hardened-container run
  [39911749036](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39911749036)
  executed the actual Linux selections but failed request participation:
  the existing witness path under the synthetic queue root was 117 bytes,
  exceeding Linux's 108-byte Unix-socket structure. The resulting interrupted
  transaction correctly fenced later work; it was not cleared or replayed.
  That run remains failed. Required PR guards and eligible units passed for
  its exact source, independently of the missing native acceptance.
- Fixed the canonical witness's address allocation, not the queue or execution
  owner. Short legacy Unix paths stay unchanged. A long path uses an owned
  private temporary directory within the native byte budget, preserving the
  authenticated original root/request and cleaning the endpoint on close.
  Clients verify private ownership/permissions, while unavailable short
  temporary storage fails explicitly. Windows named-pipe behavior is unchanged.
- Both existing consumer changefiles cover the shared driver. Tests cover
  encoded-byte length, legacy compatibility, unavailable temporary paths,
  listener-construction cleanup and live long-root participation/refusals.
  The current Windows driver/routing slice passed 63 cases with nine explicit
  native-platform skips and sandbox retirement; lint and module-size checks
  passed. The next exact-source Linux run also selects the shared driver's
  core and transport tests through the same hardened wrapper, not host-side
  integration or an enlarged required-PR test portfolio.

### 2026-10-23 - Native Linux passes; preserve independent work after review findings

- Exact-source run
  [39914962618](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39914962618)
  at `afb2d47f7ab156c3e6ab9ba6d31624c46a91eda9` passed all three actual
  Linux container targets: Worktrees, Manager and the shared driver. This
  includes live long-root participation and native Bash handoff. The disabled
  Windows rotation and shared-issue reporting were correctly skipped, not
  treated as qualification.
- Copilot identified two concrete orchestration misses. A denied Worktrees
  publication must not suppress independently eligible repository bootstrap
  and reconciliation. Likewise, an unrelated reconciliation failure must not
  suppress eligible staged Worktrees apply. The revised executor continues
  those canonical phases, retains the refusal diagnostic and returns a failed
  aggregate outcome. Acquisition failure and unhandled custody failures are
  not converted into permission to continue.
- Added managed-refusal and phase-failure regressions, then re-ran the full
  local composed slice: 209 passed, 25 explicit platform/native skips, with
  sandbox retirement. Four actual healthy handoffs completed in 18.36 seconds
  total before the gated installer was released. Their existing 20-second
  per-handoff limit, installer gate and production deadlines are unchanged.
- Local-only investigation separated that latency contract from the explicit
  blocking compatibility command, whose completion includes serial worker
  and executor startup. Its diagnostic samples completed in 5.56--19.69 seconds;
  the result-correctness watchdog is now separately bounded at 30 seconds,
  not represented as healthy-handoff latency. Failure-only phase/request/log
  diagnostics remain; all temporary timers and source instrumentation were
  removed before publication. Final-head review, CI and Linux acceptance
  still precede merge. The operator selected local-only Windows qualification.

### 2026-10-23 - Preserve the real reconciliation failure result

- Exact-source run
  [39933889758](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39933889758)
  passed all three Linux container targets at
  `ec7f986b80faf8fb8ff484fc7b37f8dbcd8598d7`; required PR checks also passed.
  Copilot confirmed the original findings were resolved but identified a
  further concrete producer-contract gap: handled reconciliation exceptions
  returned nonzero without JSON, so automatic aggregation parsed an empty
  buffer and skipped later eligible work. The absence of an open inline
  thread did not make that reported Medium finding a passing review.
- The real apply-error handler now emits structured unsuccessful JSON and
  preserves its existing failed status fields. Execution-state errors,
  participant/custody loss and unconfirmed native cleanup remain fatal rather
  than ordinary plugin failures. Regressions call the actual handler, verify
  retained earlier results and continued eligible staged work, and verify
  that authority loss stops subsequent mutation.
- The focused producer/orchestration/routing controls passed 48 cases with
  four platform skips. Full producer, acquisition and automatic-phase
  coverage then passed 88 cases with one native-lifetime skip.
  Deployment-policy regression coverage passed all 106 cases in two complete
  partitions (76 and 30), using the canonical short Windows fixture layout.
  Earlier diagnostic runs with oversized fixture paths or incomplete
  aggregate execution budgets are not counted as passing. Individual test
  and production deadlines were not widened for those policy checks.

### 2026-10-23 - Keep unconfirmed acquisition cleanup with the canonical owner

- Exact-source run
  [39939723023](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39939723023)
  passed all three Linux targets at
  `587775c90c0d91ffc5986ac2070713f43ba3d95b`; required PR CI passed too.
  The subsequent review identified another concrete custody boundary despite
  its empty inline-issue list: acquisition caught `OwnedCleanupError` through
  the generic subprocess-error handler. It must not become an ordinary
  unsuccessful download followed by more queued installation.
- Acquisition now rethrows that exact error, retaining its original native
  process/Job control. Borrowed JSON execution registers cleanup responsibility
  with the existing transaction witness before executing and releases it only
  after normal completion/result publication. A participant that abandons
  execution cannot regain mutation authority; disconnect or a converted
  integer exit cannot discard the owner's outstanding obligation. The same
  canonical journal records interruption and fences later queued work under
  its existing positive-settlement policy.
- This adds neither a queue nor another mutation lock/owner. Scope control
  requires an explicit owner acknowledgement, so an unsupported predecessor
  refuses before mutation instead of silently ignoring the responsibility.
  Existing native-foundation ownership assertions retain their wire contract.
- Real-process regression coverage has a product abandon execution and exit
  with an ordinary nonzero integer: its request remains interrupted, and the
  next queued product never executes. Separate controls verify normal nonzero
  completion, nested scopes, downlevel refusal, native-control retention and
  both borrowed JSON-adapter branches. The combined local Windows slice passed
  200 cases with 11 explicit native/platform skips and sandbox retirement,
  including native-foundation compatibility and actual healthy handoff.
  Lint and module ceilings passed. Final-source review and CI remain required.
