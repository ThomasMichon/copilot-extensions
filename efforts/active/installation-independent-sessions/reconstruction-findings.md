# Reconstruction Repair Register

Parent: [Installation-Independent Sessions](README.md), #8669.

This register records the generic findings from actual program removal and
reconstruction. Concrete host identity, backup material and private adopter
details remain in the private operational record. It is a fix/validation
roster, not a declaration that the parent effort is complete.

## Controlling operator direction

The [literal clarification](inception-transcript.md#operator---reconstruction-findings-and-cooperative-task-standby)
requires tasks and registrars to **run, but do no work** under the applicable
KillSwitch. They wait and check occasionally, then resume their existing loops
and subscriptions after clearance. OS-level disablement is not this mode.
Current independent scheduler/service disablement must not be silently undone.

The agent used `Disable-ScheduledTask` during cleanup. That disables manual
starts as well as future triggers. The bootstrap killswitch file was unchanged;
the scheduler mutation and the retained maintenance notice were distinct actions.

| Operation | Task-registration contract |
|-----------|----------------------------|
| Maintenance hold | Installed tasks remain runnable but cooperatively do no work until clearance. |
| Normal in-place update | Preserve registrations and their enablement intent. |
| Uninstall | De-register the removed component's owned tasks. |

The manual program wipe retained registrations. It was not a supported
uninstall and does not prove a supported uninstaller omits de-registration.

## Fix and validation roster

All implementation controls below are **agent-recommended** ways to satisfy the
operator's reconstruction and cooperative-standby requirements. Existing owners
retain their implementation authority; filing or linking is not claiming work.

- [ ] **R1 - Program-selection metadata survives the program wipe.**
  Confirmed: constructors attempted retention of absent generations named by
  `current-version` and `last-known-good`, refusing ordinary reconstruction.
  Backing up only those stale selectors allowed normal construction. Extend
  #1886's uninstall classification and #6464's repair conductor; preserve
  configuration, durable data and live custody. Prove fresh installation,
  intentional removal, partial reconstruction and interrupted retry without
  fake slots, guard removal or broad data deletion. Qualify actual task
  de-registration by supported uninstall separately from registration-preserving
  in-place update.
- [ ] **R2 - Disabled-task cold-start boundary.** #9357.
  Confirmed source behavior: Bridge calls `Start-ScheduledTask` for an existing
  task without testing enablement; the script uses terminating errors. A
  reconstructed runtime was selected while update exited nonzero; the daemon
  later started and current-content reconciliation passed. The disabled-task
  explanation is probable, not an original-exception receipt. Reproduce in a
  controlled fixture and preserve explicit disablement. Integrate cooperative
  standby through #7694/#6820, not automatic task enablement or a direct-spawn
  bypass.
- [ ] **R3 - Nested installer failure loses its explanation.** #8370.
  Confirmed: the containing update log and target event retained the nonzero
  result but not the inner installer's exception. Consume this existing owner's
  independent per-attempt logging and discovery contract, including native
  Windows nested Manager/engine execution, interruption and handle lifetime.
  Later successful reconciliation must not erase the earlier unknown cause.
- [ ] **R4 - Opaque native-GC deferral.** #9358.
  Confirmed repeated message: `native-reconcile-failed (RuntimeException)`.
  This proves inadequate diagnostics, not unsafe GC or a failed build.
  Retain the native result and boundary through #8370's logging; distinguish
  expected deferral from required reconciliation failure, and keep uncertain
  live instances protected through retry.
- [ ] **R5 - Repository readiness versus local serving health.**
  #6389 with #1941/#6082.
  Observed differential: Index installer status said inactive/unconfigured/not
  running while the selected local endpoint returned healthy with a live PID.
  Classify the exact contexts before calling this a defect. Report scope and
  serving evidence distinctly; never weaken opt-in or start work to force
  agreement. Prove context-correct inactive and configured positive controls.
- [ ] **R6 - Complete reconstruction includes adopter-owned program surfaces.**
  #1886/#6464 integration acceptance.
  Classify active/previous installation records, generated global command
  wrappers and non-core clients alongside version directories. The private
  adapter recovery exposed stale publication/wrapper ownership collisions;
  its precise repair remains with that adapter's owner. Prove that the declared
  owner can restore each selected footprint without guessing paths, overwriting
  foreign commands or resetting retained configuration, cursors or memberships.
- [ ] **R7 - Maintenance preserves runnable control loops.** #7694/#6820,
  consuming #9357.
  Implement and qualify the operator's cooperative no-work mode. Preserve
  progress, subscriptions and pending-effect custody across the hold, check it
  before work admission, and resume only after applicable clearance and restored
  dependencies. The standby boundary must not pin a disposable runtime merely
  to wait. No parallel banner, scheduler, polling service or execution authority.
- [ ] **R8 - Normal generated bytecode blocks supported Manager removal.** #9375.
  A current-release mux witness answered a native terminal challenge with no
  runtime-Python execution ancestor. Supported uninstall preview then refused
  before effects: both completed Manager generations had only added CPython
  caches, while every sealed artifact and slot identity matched. Qualify narrowly
  source-bound generated-cache attribution, keeping sealed entries, unknown
  files, links, plan revalidation and live-process custody protected. Never
  delete caches manually or re-seal an installed generation to manufacture
  removal acceptance. The witness remains running; no removal is claimed.

- [x] **R9 - Per-request custody invalidates source-wheel reuse.** #8669.
  An ordinary Windows candidate exhausted the shared source-wheel deadline
  during build-tool-tree verification; the previously selected generation
  remained intact. The 333/334 installer code is identical, and the same
  180-second budget covers tooling, the complete source batch and validation.
  Controlled native admission proves that `UPDATE_DRIVER_WITNESS` entered the
  offline build environment and cache identity, changing keys for otherwise
  identical requests. The local correction excludes that control metadata only
  from offline builder inputs; constructor admission retains it. Key controls
  and actual fresh-generation wheel reuse pass while ordinary environment and
  source changes remain bound. This is a confirmed cache defect, not proof it
  is the sole contributor to the observed deadline. Finish integrity regressions,
  review/release and the actual installed batch before closing the live gate;
  do not relax integrity checks or deadlines to manufacture acceptance.
  The source correction merged in #9441 after required CI and real-backend
  Linux/Windows acceptance. Normal promotion #9451 contains its exact builder
  bytes. The authorized installed Worktrees 1.24.335-dev1 candidate passed the
  actual sixteen-wheel cold batch (16 built, zero reused; 168.030 seconds),
  own-interpreter verification and publication. Its receipt independently
  records sixteen source wheels; sampled installed modules each have two
  filesystem hardlinks. This closes the confirmed cache-identity defect and
  installed-batch gate, not a live warm-cache-hit claim or every possible
  deadline contributor. The containing unified update separately failed in
  Vault reconciliation; R2/R3 and the parent live gates remain open.

- [ ] **R10 - Retire legacy broker adoption from normal installers.** #9567,
  following #9542.
  The released native-spawn architecture no longer requires Task-broker
  adoption, but Bridge still invokes that adapter after slot completion and
  before selection. A subsequent unified reconstruction produced a completed
  Bridge candidate, then exited nonzero before publishing its selector or
  deployment manifest. The inner exception was not retained, so the exact
  failure is not retroactively diagnosed from that ordering alone. Runtime
  reconciliation then refused participant custody and the request remained
  interrupted, not settled or successful. Remove obsolete normal-adoption
  effects through reviewed source while retaining legacy recovery/removal,
  native custody, completion/publication and independent task-disable intent.
  Keep #8370/#8826's diagnostic owner and #9357's separate lifecycle boundary;
  no direct installer, fabricated witness or manual candidate activation.
  Source repair #9571 merged at `94a648c821dfe5746ac423716e370d79a35fcf82`
  after all 49 selected current-head native/offline/lifecycle controls,
  required CI and fresh Copilot approval passed. The source worktree is
  finalized and its parent claim settled. Promotion #9849 released exact
  Dev `94a648c8` on Main `a246b0587192bb4f754f4e6906fcf630ac42f343`;
  immutable source assets exist at the same Dev identity. All eight reviewed
  consumer files are preserved apart from declared engine-path materialization,
  and all seven released engine copies match canonical. #9567 is closed as
  the completed bounded source follow-through. Installed canonical
  reconstruction and interrupted-owner recovery remain open;
  this finding is not closed by the source merge alone.
  The separately authorized original contributor follow-through #9501 also
  merged at `cf6c0ab189c9c537bd1df2c09249401187ff53d9` after exact-head
  Windows/Linux native qualification, current required CI, full Copilot
  approval, complete thread resolution and separate Maintainer acceptance.
  Its source worktree is finalized; original attribution and history remain.
  This completes that source lane, not selected-host reconstruction or
  interrupted-request recovery. Each OS continues to own only its own cleanup.
  Normal promotion 41381305753 and generated PR #9937 now release exact
  Dev `cf6c0ab1` on Main `98db6b0998ac03de52d8da06c3d687d2b449eda7`,
  with published complete-source assets at that Dev identity. Installed
  acceptance and existing-owner recovery are still separate open gates.

- [ ] **R11 - Bootstrap preparation and request custody survive application replacement.**
  Source repair #10559 merged at
  `5aea4b4010c08846f92bd8d9b5d14c7b1accfa19` after exact-head approval,
  required CI, all four native bootstrap/retention jobs and both legacy-monitor
  jobs passed. The current controller retains the complete preparation
  source/assets and effective installed-CLI anchors, preserves holds and
  per-request identity, and keeps controller-only options out of older
  application argument/environment contracts. Stable input/asset revisions
  may reuse bytes without coalescing distinct pending requests. The
  [source journal](<journal/2026/11.06 bootstrap-custody-source.md>) records
  the finite proof and unsupported old-relative-CLI/deleted-cwd case.
  Verify the containing normal release, then qualify the authorized ordinary
  installed reconstruction and existing-owner recovery independently. Neither
  source success nor a new request identifier settles an interrupted request
  or authorizes replay, manual activation or a host rollout.

## Evidence limits and remaining live gate

Ordinary reconstruction, data-preservation checks and subsequent service health
are recorded separately from initial failed attempts. A successful current-slot
reconcile does not prove a full unified update succeeded. The fresh selected-host
baseline contained no active mux panes, so it cannot establish same-process
interactive mux survival. The parent Plan/Validation retains the controlled
surviving-session witness, native standby, complete launch parity and both
installation cycles.
