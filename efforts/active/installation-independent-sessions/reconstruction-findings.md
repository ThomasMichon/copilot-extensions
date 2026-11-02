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

- [ ] **R9 - Per-request custody invalidates source-wheel reuse.** #8669.
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

## Evidence limits and remaining live gate

Ordinary reconstruction, data-preservation checks and subsequent service health
are recorded separately from initial failed attempts. A successful current-slot
reconcile does not prove a full unified update succeeded. The fresh selected-host
baseline contained no active mux panes, so it cannot establish same-process
interactive mux survival. The parent Plan/Validation retains the controlled
surviving-session witness, native standby, complete launch parity and both
installation cycles.
