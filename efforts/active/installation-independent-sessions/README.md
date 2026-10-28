# Installation-Independent Sessions

- **Slug:** `installation-independent-sessions`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed slices targeting `dev`
- **Created:** 2026-10-24
- **Status:** Active
- **Vision:** `visions/session-hosting/README.md` - installation-independent-running-sessions; `visions/plugin-services/disposable-runtimes/README.md` - shared-maintenance-banner and service-independent-agent-standby; `visions/plugin-services/README.md` - process-count-scales-with-services-not-sessions and hooks-and-callbacks-are-transient
- **Umbrella issue:** #8669
- **Related issues:** #5579 (existing launch-depth owner), #8410 (Linux uninstall), #8312 (Windows updater handoff), #8864 (supported Manager uninstall), #9357 (disabled-task cold start), #9358 (native-GC diagnostics), #9375 (generated-bytecode removal); existing reconstruction/maintenance owners in [the repair register](reconstruction-findings.md)

## Guiding Intent

A running muxed Copilot session belongs to its execution leg, not to the
continued existence of either launcher installation. Removing and replacing
worktree-manager and agent-worktrees must not disturb that session. Initial
launch and final exit delegate policy to the compatible implementation
selected at that boundary, without pinning an old interpreter or mutable
installed shell script for the session's lifetime.

Completion means production implementation plus actual same-process,
interactive survival and correct final lifecycle handling across real
uninstall/reinstall and update, not a copied-fixture demonstration or a merged
source change alone.

During an intentional maintenance window, the surviving agent can understand
why its tools disappeared without invoking those tools. It preserves progress
and obligations, pauses affected operations, reads the local-machine banner
through native facilities, waits for the window to clear, and resumes against
the restored capabilities. Maintenance-aware standby complements process
survival; neither substitutes for the other.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Installation-survival driver | Canonical plan, bounded implementation, serial PRs, validation and release acceptance | #8669 and this effort |
| Existing lean-lifecycle owner | Retains wait-only forwarding and launcher-depth reduction | #5579 and `efforts/active/lean-session-lifecycle/README.md` |
| Existing prerequisite owners | Retain already claimed installer/updater repairs | #8410 and #8312 |
| Linux baseline worker | Evidence-only disposable installation/uninstall baseline at the reviewed source pin | Reports to Installation-survival driver; no source or host changes |

## Coordination

- **Topology:** serial per-slice PRs; each slice leaves the default branch usable.
- **Host (owns PRs):** Installation-survival driver.
- **Delegates:** Linux baseline worker owns one disposable Linux baseline and
  its evidence/cleanup only; no source edits, git mutations or host operations.
  The driver retains production wrappers, hooks and Windows validation.
- **Boundary:** #5579's existing owner retains removal of wait-only forwarding
  layers. Agree the shared launch contract and overlapping edit ownership
  before changing those paths; no competing implementation or silent transfer.
- **Handoff:** read this README, the first unchecked Plan/Validation items,
  and the current journal; preserve open PRs, test resources and unresolved
  lifecycle effects. A phase or release is not whole-effort completion.
- No host uninstall, shared-daemon restart or installed rollout is authorized
  merely by this plan. The operator subsequently authorized one selected live
  Windows test host for the [live round trip](live-roundtrip.md), including
  backups, uninstall/reinstall, re-release and necessary promotion fixes.
  This does not authorize effects on other hosts or waive repository, OS
  privilege, connectivity, custody or data-preservation gates.
- Reuse #9022's conduct-guidance companion and #9040's reviewed shared YAML
  banner contract, including their retained automatic-maintenance owners.
  This effort integrates standby with session survival; it does not introduce
  a second banner, scheduler, updater or maintenance authority.

## Context

- The earlier exit-output repair was released and installed. This effort
  preserves its quiet successful-exit behavior rather than re-solving it.
- Six prior Linux copied-wrapper controls passed, including process/terminal
  continuity, start-under-A/end-under-B and absent-handler controls. Those were
  feasibility evidence, not production installation-removal or Windows proof.
- #8410 records a literal Bash uninstall failure that blocked the real
  installation experiment. #8312 records an independent Windows updater
  handoff failure. Keep their existing ownership and reproduce only the
  prerequisites the current slice needs.
- Lean Session Lifecycle Phase 3 already owns wait-only process reduction and
  launch parity. This effort adds the stronger installation-removal survival
  requirement; it does not take over handoff queues or monitor optimization.
- This plan extends the session-hosting vision with installation-independent
  running sessions, and closes the existing transient-hook/process-lifetime
  intent. Follow the installation-cell, uniform runtime resolution,
  process-slot ownership, lifecycle logging and performance patterns. No new
  daemon, machine-global selector or parallel lifecycle authority is needed.

## Request

**Settled premise (summary):** keep the mux server -> copied session wrapper
-> Copilot construction stable while both launcher systems can disappear or
be replaced. Use redirectable start/end hooks at the boundaries, and prove the
contract in a real clean room.

The full literal inception request is preserved in
[inception-transcript.md](inception-transcript.md). Latest direction:

> Great. Continue driving my proposal as a proper effort.

The operator confirmed the slug `installation-independent-sessions`.

**2026-10-24 prerequisite decision:** the operator selected
`supported-uninstaller-first`:

> Add a supported product uninstaller first (larger scope)

The selected contract and its explicitly agent-recommended refinements are in
[manager-uninstall.md](manager-uninstall.md). This adds a prerequisite, not a
replacement for the continuing installation-survival objective.

**2026-10-25 priority and maintenance-window direction (summary):** prioritize
this isolation work and extend the vision so an agent whose supporting tools
disappear can pause, read a local-machine maintenance banner without those
tools, wait while the YAML window is active, and resume once it clears.
The [literal follow-up](inception-transcript.md#operator---priority-and-maintenance-window)
is retained without converting the priority statement into permission for a
host update, removal or rollout.

The standing intent already includes this extension through reviewed #9042.
The existing [shared YAML banner contract](../source-managed-runtime-installs/maintenance-banner.md)
is the implementation-contract owner. Its explicit same-window completion and
native 30-minute standby policy are **agent-recommended bindings**, not extra
literal requirements added to this request.

The detailed fencing, negative controls and rollout admission below are
**agent-recommended implementation/validation refinements**, not additional
verbatim operator requirements.

**2026-10-26 selected live-host direction (summary):** use the explicitly
selected Windows test machine to audit the install footprint, separate durable
state from reconstructible runtime data, back up the data first, and carefully
uninstall services/tools while preserving SSH-Gateway connectivity and muxed
Copilot. Prove a clean middle, then reinstall and reconnect to the retained data
so execution carries on. The operator explicitly authorized uninstall,
reinstall, re-release and necessary Main-promotion workflow repairs until this
round trip works. The [literal follow-up](inception-transcript.md#operator---selected-live-test-host)
redacts only the private host name. Concrete host identity and operational
records remain private; the generic execution contract is [live-roundtrip.md](live-roundtrip.md).

**Legacy-session decision:** after the agent identified runtime-Python
ancestors in existing sessions, the operator explicitly selected controlled
closure and relaunch **after verified backups and fixed-launcher
qualification**, preserving resumable state. The [literal selection](inception-transcript.md#operator---legacy-session-migration)
and live charter retain that ordering. Migration is not same-process survival;
the qualified replacement's later uninstall/reinstall test must preserve its
own original processes.

**Latest execution direction (summary):** continue burning down this same
effort to the selected-host live test. Admission is per worktree, only until
its mux is discoverable; contenders join that mux and different worktrees
launch in parallel. Record the active native conversations, close legacy
muxes, uninstall and clean installation leftovers, freshly install and restore
the exact active set using the new system, then uninstall, clean and reinstall
again around those replacement sessions. Cleanup explicitly excludes runtime
state and configuration. The [literal follow-up](inception-transcript.md#operator---per-worktree-admission-and-two-cycle-live-test)
retains these constraints; private host identity stays outside this repository.

**Reconstruction and task-lifecycle clarification (summary):** track all
reconstruction findings in this effort, reusing existing owners and adding the
two scoped issues #9357/#9358. Installed background tasks should remain runnable
but do no work while the applicable KillSwitch is active; they wait, check
occasionally, then resume loops/subscriptions on clearance. This is not
OS-level disablement. Separately, uninstall de-registers owned tasks, whereas
normal in-place update preserves registrations. The [literal follow-up](inception-transcript.md#operator---reconstruction-findings-and-cooperative-task-standby)
and [repair register](reconstruction-findings.md) retain the evidence and limits.

## Plan

### Phase 1 - Reviewed contract and shared ownership

- [x] Resolve the target's effort adoption, read its conventions, deduplicate
  existing efforts/issues and confirm the canonical slug with the operator.
- [x] Capture the literal request, distinguish feasibility evidence from
  acceptance, and open the bounded tracking issue.
- [ ] Coordinate the shared contract and edit boundary with #5579's existing
  owner; preserve separately owned installer/updater repairs.
- [x] Land this proposal and standing-intent amendment through normal review
  before production implementation.
- [ ] Inventory current mux/no-mux, direct/new-window, resume and remote
  launch consumers; record the smallest compatible boundary and migration in
  [launch-contract.md](launch-contract.md).

### Phase 2 - Literal disposable-installation reproduction

- [x] Review and implement the supported Manager uninstall prerequisite
  (#8864) in [manager-uninstall.md](manager-uninstall.md), then return to this
  same real-removal baseline.
- [ ] Reproduce current behavior with real installed A, an interactive owned
  session, actual uninstallation of both systems, installed B and final exit.
- [x] Resolve or coordinate #8410 only as the required Linux uninstall
  prerequisite; do not substitute deleting a prototype fixture for uninstall.
- [x] Qualify #9253's bounded producer-complete removal and actual macOS
  entrypoints, with original capture and unchanged deadlines; retain the
  [merged-source evidence](<../source-managed-runtime-installs/journal/2026/10.27 native-runtime-removal.md>)
  separately from same-process interactive and native Windows acceptance.
- [ ] Record baseline process identities, terminal interactivity, installed
  file removal and locks, and current failure without altering host installs.

### Phase 3 - Production launch-owned wrapper and boundary hooks

- [x] Land finite seed/backend provider groundwork and the dormant Windows
  adapter with existing seed/execution authorities (#9242). This prerequisite
  does not activate callers or remove either production runtime waiter.
- [x] Activate and qualify the normalized Windows/PowerShell 7 copied consumer
  for executable and same-host-script backends (#9288), including interrupted
  waits and normal script return without an explicit exit.
- [x] Land provider-first literal preparation transport and selected-runtime
  contract discovery (#9318), without activating a POSIX lifetime owner.
- [x] Land the dormant Bash frame reader and copied POSIX setup assets (#9321);
  keep bounded callback execution and native lifetime activation separate.
- [x] Land and natively qualify the dormant finite POSIX phase transport
  (#9395), including shared read/EOF deadlines and provider-group settlement.
  Production selection, exact process birth and backend lifetime remain open.
- [ ] Remove both deferred-setup and final-backend runtime-Python waiters.
  Reuse finite seed preparation, existing seed custody and execution receipts;
  the native shell owns the actual child handle and wait. The normalized
  Windows slice is merged; remaining legacy consumer parity is still open.
- [ ] Fence only same-worktree check/create races until mux discoverability.
  Recheck and join on contention; distinct worktrees remain parallel and no
  launch lock survives for Copilot's lifetime.
  - [x] Land and qualify the shared Windows Picker/detached creation primitive;
    preserve the separate seed and lifecycle owners. POSIX parity and removal
    of broader tracked-launch serialization, plus the separate `pane_create`
    new-session fallback, remain separate unchecked work.
  - [x] Land provider-first per-worktree lifecycle exclusion in native launch
    and tracked retirement, retaining the legacy repository barrier (#9339).
    This does not activate narrower launch locking for independently running
    retirement peers.
- [ ] Prepare launch arguments/environment before the stable wrapper; copy
  its self-contained platform shell into launch-owned storage outside both
  removable runtime/config roots, with installer/pruning ownership explicit.
- [ ] Resolve attributable, installation-scoped start/end entry points afresh
  at each boundary; no cached runtime Python, daemon endpoint or imported
  installed helper survives merely to wait.
- [ ] Keep the real mux server, wrapper, Copilot and kernel containment alive
  independently of installer removal. Preserve terminal, arguments, profiles,
  Ctrl+C and real exit code; coordinate wait-layer changes with #5579.
- [ ] Implement bounded exact-incarnation lifecycle admission through existing
  owners. A delayed old end cannot retire a replacement; observed exit is
  distinct from crash/loss, human cancellation and terminal disconnection.
- [ ] With no compatible handler at exit, close quietly without provisioning
  or falling through to another installation; do not falsely record delivery.
- [ ] Reconcile restart and missed-boundary evidence through the existing
  lifecycle owner, preserving unknown effects rather than blind replay.
- [ ] Land directly related tests, docs and required changefiles.

### Phase 4 - Native and clean-room acceptance

- [ ] Run the complete matrix in Validation Plan on production code and real
  disposable installations, including native Windows rather than Linux proxies.
- [ ] Exercise existing consumer paths and regressions; retain source/runtime
  identities and evidence that proves which build each result exercised.
- [ ] Qualify real mux creation-client output-handle lifetime, including a fast
  successful client return before the first discovery poll and inherited
  descendant output handles. A healthy pane must not be retired or lose its
  handoff because only a creation client or its output transport failed.
- [ ] Fix failures found in the touched contract; transfer only genuinely
  independent work to named tracked objectives without dropping acceptance.

### Phase 4b - Maintenance-aware session standby

- [x] Reconcile the expanded request with the already reviewed disposable-runtime
  vision and shared YAML-banner proposal; retain #9022/#9040 and existing
  maintenance implementation ownership rather than opening a competing design.
- [ ] Coordinate the YAML reader/guidance migration with those owners before
  exercising it. The earlier JSON-only source guidance is not YAML capability
  or evidence of delivery to an already-running session.
- [ ] Integrate the normal conduct projection and native-tool banner read with
  the actual surviving Copilot session; no runtime, service, command, network
  path or credential broker under maintenance may be needed to enter standby.
- [ ] Preserve progress, exact launch identity, claims and pending effects while
  affected operations pause. Do not reinstall, reprovision, repeatedly retry
  missing commands, launch replacement agents or take over the maintenance.
- [ ] Exercise the reviewed native standby/all-clear contract, then refresh
  selected invocation/capability evidence before resuming. Clearing the banner
  does not replay unknown mutations or enable independently disabled policy.

### Phase 5 - Release, installed acceptance and closure

- [ ] Audit executable scripts, configuration, durable runtime state and
  disposable temporary state as separate lifetimes. Record mixed storage
  boundaries and derive follow-up work against the disposable-runtimes vision;
  do not turn this audit into a prerequisite for the authorized live cleanup.
- [ ] Resolve the [reconstruction repair register](reconstruction-findings.md):
  stale selectors and wrappers, cold-start/maintenance interaction, lost
  diagnostics, opaque GC deferral, readiness scope and complete owner coverage.
  Reuse linked existing owners; tracking does not claim their implementation.
- [ ] Implement the operator's cooperative task/registrar standby requirement:
  remain runnable, check the applicable KillSwitch, do no work and wait while
  held, then resume existing loops and subscriptions after clearance. Do not
  substitute OS-level task disablement, silent re-enablement or a direct-start
  bypass. Coordinate #7694/#6820 and #9357.
- [ ] Keep lifecycle operations distinct: supported uninstall de-registers
  owned tasks; a normal in-place update preserves registrations and enablement
  intent; a maintenance hold keeps installed tasks runnable but quiescent.
  The manual program wipe is not supported-uninstaller conformance evidence.
- [ ] Verify containing normal Main release and exact released behavior.
- [ ] Obtain any required selected-host rollout approval, use the canonical
  updater, and validate a fresh native launch/exit. Existing sessions may
  still carry their older wrapper and are not rollout evidence.
- [ ] Execute the explicitly authorized selected-host [live round trip](live-roundtrip.md):
  protected connectivity and consistent backups first, attributable removal,
  recorded clean middle, reinstall/state reconciliation and same-session proof.
- [ ] For the authorized legacy migration, preserve and restore exactly the
  selected active worktrees and associated native sessions. Verify attached
  `worktrees copilot` and detached `worktrees embody` share create-or-resume
  semantics with the qualified wrapper and never duplicate a live execution.
- [ ] Before the first cycle, record the current exact active worktree/native
  conversation/mux set privately and verify restorable backups and fresh
  independent protected SSH-Gateway connectivity.
- [ ] First cycle: gracefully close recorded legacy muxes, use supported
  uninstallers, classify and remove only in-scope straggler processes and
  installation detritus, record the clean middle and freshly install the
  qualified release without removing state or configuration.
- [ ] Restore exactly the recorded active set through the new system and
  verify the associated native conversation IDs, both command entry points,
  terminal behavior and replacement mux/wrapper/Copilot process identities.
- [ ] Second cycle: uninstall, clean identified installation leftovers and
  reinstall again while those replacement sessions remain the same running
  processes. Verify interactive continuation, state/config preservation,
  protected connectivity and final current-handler lifecycle delivery.
- [ ] Re-read every Plan/Validation item, promote durable truth to owner docs,
  settle owned test/PR resources, mark Done and archive through normal review.

## Validation Plan

All detailed controls below are **agent-recommended** ways to prove the
operator's installation-survival contract.

- [ ] Unit tests cover boundary dispatch, installation identity, exact launch
  incarnation, duplicate/stale end handling, missing/unsupported handlers and
  unchanged literal argument/environment/exit propagation.
- [ ] A simple subprocess integration test invokes the real copied shell and
  real redirectable handler; mocks cannot bypass the resolution being tested.
- [ ] Real overlapping same-worktree attempts create one mux and join it;
  different-worktree launches overlap independently. Admission releases when
  the mux is discoverable, not when Copilot exits.
  - [x] Windows native component controls cover Picker and
    `sessions.mux_new_session`, actual shared kernel-lease release, literal
    arguments and failure handoff retention (#9320). This does not qualify
    actual PSMux, complete tracked-launch parallelism or the pane fallback.
  - [x] Expand native launch and all tracked-retirement readers to the same
    separate per-worktree lifecycle lease while retaining the legacy repository
    barrier (#9339).
  - [ ] Qualify independently running retirement peers before migration
    removes that barrier; source/payload freshness is not peer support.
- [ ] Linux clean room: install A, launch an interactive owned session,
  actually uninstall both systems, prove their runtime roots are gone, install
  B, and exit. The same mux, wrapper and Copilot processes remain interactive;
  A handles start, B handles end exactly once for the right incarnation.
- [ ] Native Windows repeats the actual uninstall/reinstall test, including
  executable/script locks and the wrapper-owned kill-on-close Job lifetime.
- [ ] Live update/rollback replacement preserves the same running session;
  exit resolves the then-current compatible handler in the same installation.
- [ ] Exit while neither system exists is quiet, bounded and non-provisioning.
  It preserves Copilot's exit code and honest unconfirmed-end state.
- [ ] An unsupported or foreign installation never becomes an implicit
  fallback. Two installations and concurrent sessions do not alias handlers.
- [ ] Delayed/duplicate end for A cannot unregister replacement B; observed
  nonzero Copilot exit is not misclassified as unobserved crash or user cancel.
- [ ] Handler/mux-management-daemon absence and restart do not kill the live
  mux server; recovery neither double-launches nor falsely retires a session.
- [ ] Ctrl+C, terminal detach/reattach, resume, quoted/double-dash arguments,
  interactive profiles and no-mux/new-window/remote consumers retain their
  existing supported semantics; any intentionally unsupported branch is explicit.
- [ ] Removing the containing wrapper genuinely tears down its owned children
  where required; survival is not achieved by discarding kernel containment.
- [ ] Measure persistent process ancestry and boundary latency; no new resident
  waiter, helper polling loop or unowned detached process replaces the removed
  coupling. Sparse native agent standby prompts are a separate maintenance
  behavior, not a resident session-lifetime dependency.
- [ ] The same running agent receives usable guidance before an intentional
  outage, detects missing tools, reads the retained YAML banner natively, pauses
  affected operations and waits without repair or dependency on the removed
  payload, service, binstub or credential broker.
- [ ] In isolated Windows/Linux maintenance controls, the reviewed single native
  standby poll reports material changes without a helper daemon or busy loop.
  Unavailable native scheduling is explicit rather than silently substituted.
- [ ] An active, stale, malformed, unreadable or replaced window cannot
  accidentally release an established hold. Applicable clearance ends only the
  owned standby poll; restored capability is verified before continuation,
  independent disables stay disabled, and unknown effects are not replayed.
- [ ] Background tasks and registrars remain runnable across a maintenance
  hold. Prove no work is admitted while held, occasional checks continue, and
  clearance resumes the existing loops/subscriptions without lost custody,
  duplicate workers, scheduler enablement changes or dependence on a removed
  runtime merely to wait. Preserve a separately explicit OS/service disable.
- [ ] Actual supported uninstall removes owned task registrations, while
  ordinary in-place update preserves them and their enablement intent.
  Exercise this separately from manual program-folder cleanup.
- [ ] Every [reconstruction finding](reconstruction-findings.md) has a resolved
  implementation/validation result or an explicit transfer to its named
  tracked owner; later success does not retroactively diagnose an earlier error.
- [ ] Existing exit-noise controls still pass: successful finalization stays
  quiet, cleanup remains a bounded summary and updated batch tails remain valid.
- [ ] Normal release, explicitly approved selected-host installed validation,
  durable evidence and final effort review pass before declaring Done.
- [ ] Selected live host: verified restorable backups precede removal;
  SSH-Gateway and the same mux/Copilot processes remain usable at every phase;
  owned runtime/services are absent at the clean middle with explicit protected
  exceptions; reinstall preserves durable state and pending-effect custody.
- [ ] Both selected-host installation cycles are recorded separately: legacy
  closure/restoration is migration evidence, while the replacement's later
  same-process survival is installation-independence evidence. Manual scrub
  never deletes runtime state or configuration.

## Proposal

Keep policy in short-lived current handlers and the execution lifetime in the
small copied wrapper and OS-owned resources. The stable handoff contains only
the launch identity, installation identity and data needed by the existing
lifecycle owner; it is not another coordination store.

The first reviewed contract must specify bounded boundary calls, storage
ownership and reclamation, supported-handler compatibility and affirmative
end semantics. The actual uninstall test decides whether the mux executable,
wrapper or inherited process resources still depend on removable installations.
Keeping a version directory alive is not an acceptable substitute.

The maintenance strategy consumes the existing shared banner and normally
projected conduct guidance. Its outage explanation and hold remain readable
outside removable supporting installations. It adds no new execution authority,
and does not delay final Copilot exit indefinitely merely to deliver a lifecycle
notice; the existing bounded/quiet absent-handler contract remains intact.

## Journal

### 2026-10-28 - Provider-owned setup birth prerequisite

- The continuing POSIX trace found that setup needs the existing shell PID's
  canonical birth token before any preparation effect. Resolve an omitted
  setup token inside the existing finite provider with `locks.process_start_time`,
  not a second Bash ABI or prerequisite identity RPC. Advertise the optional
  source through the existing read-only contract descriptor. Existing
  caller-supplied tokens, default JSON, NUL frames and backend callbacks remain
  unchanged; no production consumer omits the token yet.
- New regressions reproduced omitted-token refusal in both seeded and
  admission-only setup. A related existing gap left only `execution-requested`
  when the owner's birth changed during preparation. The candidate now records
  that post-effect uncertainty and never records a confirmed setup spawn or
  releases staged intent for replay. Invalid/unreadable identity still refuses
  before receipts or seed effects.
- The focused Windows provider/pending-seed packet passes 68 controls, including
  actual contract-description execution, current-kernel identity reads,
  JSON/NUL compatibility and retained seed custody. Native Linux/macOS provider
  acceptance, required gates and review remain pending. This does not activate
  POSIX setup/backend lifetime or close fast-exit, stdin/TTY/signal, release,
  live removal, standby or selected-host gates.

### 2026-10-28 - Cold embodiment setup-asset gap

- Production tracing isolated a separate actual consumer: cold `embody` builds
  the normalized setup command and calls `mux_new_session` directly. It never
  invokes the deferred-command selector, so copied pane assets did not imply
  copied setup assets. The observed installed-script ancestry is not explained
  by executing a preview plan.
- The bounded candidate reuses the canonical Windows setup/provider selector
  in copy-only mode before mux creation. It leaves cold embodiment's existing
  launch fence, resume selection and interactive prompt delivery in charge;
  no finite seed/admission flags or second seed claim are added. Copy failure
  refuses before spawning. Preview and live-mux joins remain unchanged.
- Rebase retained the subsequently merged #9374 native-startup prompt owner.
  Its qualified seeded commands already pass through deferred preparation and
  remain unchanged by the copy-only selector. Seed-free and terminal-fallback
  normalized setup commands are still this repair's actual gap. Both native
  prompt and separate terminal-fallback orchestration controls are retained.
- The native asset control executes the actual copied PowerShell wrapper/setup
  with literal arguments, removes only disposable source fixture files from
  the synthetic backend, and propagates its nonzero exit. This is not a
  supported-uninstall or real-Copilot acceptance test. The final contained
  shell/embody/resume packet passed all 128 tests on rebased Windows source, including that
  native control; high-signal Ruff and install-contract validation also passed.
  Published as #9390 with required CI green. The first review identified an
  added duplicate Manager health probe under the lifecycle fence. Copy-only
  preparation adds no native-provider flags, so it now avoids that irrelevant
  probe while retaining the existing pane-construction health selection and
  finite native activation's peer check. A deterministic underlying-probe count
  control covers healthy and timeout results. The expanded final Windows
  shell/embody/resume/Manager packet passed all 134 tests; fresh review of the
  correction remains pending.
- Supported removal remains conservatively blocked on rooted process
  arguments. A mux retains its consumed one-shot manifest path in startup
  argv; a seed-free setup's post-backend tail uses loaded code and Git, but
  those source observations do not authorize a blanket mux/PowerShell
  exemption. No removal guard, live session or task trigger was changed.
  Proper consumer admission and the same-process clean-middle proof remain
  open parent gates, alongside the previously recorded partial update.

### 2026-10-28 - Native witness and generated-cache removal prerequisite

- A current-release muxed Copilot answered a native terminal challenge with no
  runtime-Python execution ancestor. Supported Manager preview refused before
  effects because ordinary CPython caches were added after construction; every
  sealed artifact and slot identity still matched. Created #9375 and retained
  the live witness and existing maintenance/custody constraints.
- #9376 merged after required CI and a clean owner-authored Copilot Comment
  verdict. Review caught a missing-path/null-value subset-validation defect;
  explicit membership and six non-mutating refusal controls closed it.
  Final focused controls passed 70 tests with three platform skips.
- Exact final-head fresh-Windows run
  [40654172665](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40654172665)
  exercised real cold construction, generated bytecode, supported external
  removal and reinstall. The local construction-only 300-second timeout remains
  a non-pass; no production or native-group deadline was raised.
- Re-read the parent Plan/Validation after merge. R8's source prerequisite is
  merged, but normal release, authorized installed rollout and supported live
  retry remain open. No selected-host uninstall, clean middle, same-process
  survival or cooperative task-standby gate is closed by this prerequisite.
  Continue release/rollout without overriding independently disabled triggers,
  then resume the same original-process witness workflow.
- Main promotion #9378 released Manager 0.6.187-dev1. The selected host installed
  that Manager and Worktrees 1.24.324-dev1 through the normal owner. Its original
  mux, shell chain and real Copilot retained their identities and answered a
  fresh terminal challenge after runtime replacement. This is actual update
  continuation, not uninstall survival. The containing updater still failed
  at its nested plugin phase; no complete unified update is claimed.
- Fresh backup/archive readback and seven SQLite snapshots passed with zero
  errors or volatile files. Manager supported preview now reports planned.
  Worktrees native consumer preflight still refuses an existing external mux
  as rooted. No product was removed and no guard or process was bypassed.
  Next classify the actual mux manifest/data references and descendant setup
  lifetime through existing launch/removal owners before retrying removal.

### 2026-10-28 - Finite POSIX transport qualification

- #9395 merged as `25661575c6d9757b14e46562d946c164887b40ba` after exact-head
  Copilot review `5682513310` on
  `c72607e541fc944e8a95730adb6c99f083479e9b`. Its clean owner-authored
  `COMMENTED` verdict had no open findings; the overview's pending-macOS
  observation was superseded by completed run `40686894227` on that same head.
  Linux Bash 5.2.21 and stock macOS Bash 3.2.57 each passed 66 controls with
  17 Windows-only skips; Windows passed nine setup/admission controls and the
  independent Picker-handoff control. The final unit packet passed 2,565 with
  12 deselected in 91.687 seconds, coverage recorded once.
- Both inline findings are resolved. A fresh metadata-triggered required gate
  briefly blocked managed merge; the required-check watcher confirmed all
  green before the successful retry. The normal Maintainer exception covered
  only the additional CODEOWNER vote. Post-merge reconciliation retained a
  recovery ref and proved the complete local PR delta matched the actual
  squash delta before advancing only that merged range; no unmerged work was
  discarded.
- Re-read the parent Plan and Validation Plan after merge. Closed only the
  dormant finite-transport prerequisite. Continue capability/rollback
  selection and canonical process-birth integration before activating the
  copied POSIX backend lifetime. Preserve #5579's forwarding ownership,
  existing seed/execution receipts, terminal/stdin/signal semantics and all
  real-removal, maintenance-standby and release/installed acceptance gates.
  The effort remains Active; no live installation, session or rollout changed.
- Added the dormant copied phase transport: reselect the owning root's current
  completed runtime at each boundary, retain literal reply bytes on a pipe,
  require actual successful producer exit and settle the finite provider group.
  Complete output followed by nonzero exit is still unconfirmed and clears
  the collected reply. No reply environment is applied or backend dispatched.
- The focused Windows/Git Bash framing/copy packet passed 55 controls with
  three native-POSIX skips. The selected native publication/uninstall packet
  passed nine controls. Native Linux/macOS deadline/group qualification was
  still pending; these local results are not native terminal or removal evidence.
- Native run `40676755235` passed Linux and Windows but failed on stock macOS
  Bash 3.2: process-substitution PIDs are not waitable there. Replaced that
  nonportable wait with a private per-call completion field emitted only after
  the finite supervisor has reaped its actual provider and settled its group.
  The existing deadline is unchanged; the failed attempt is not passing evidence.
- Corrected native run `40678165917` on
  `6b7f28a1541d353c2a1bb22fd44428f63af9ff6f` passed all three selected jobs.
  Linux Bash 5.2.21 and stock macOS Bash 3.2.57 each passed 53 reader/phase/copy
  controls with 17 Windows-only skips, including actual timeout/group cleanup.
  Windows passed nine setup/admission controls and the independent uncertain
  Picker-handoff control. The corrected local packet passed 67 controls with
  three native-POSIX skips; the exact-code no-actor packet passed 2,565 with
  12 deselected in 99.781 seconds, coverage recorded once. This final journal
  update changes no qualified runtime code.
- Review then found early malformed input could return refusal before the
  finite producer settled. Actual malformed-header and malformed-completion
  regressions both reproduced a still-live provider at return. The collector
  now retains and drains its private pipe through supervisor EOF before
  returning failure. Both regressions and the focused Windows/Git Bash packet
  pass: 69 controls with three native-POSIX skips. The corrected drain still
  requires fresh native qualification; neither watchdog nor test deadline
  was increased.
- Native run `40680633959` on `de066ca18e14c77e7e7b9ad4b5f79f1135e690f9`
  then passed all three jobs: Linux and stock macOS Bash each passed 55
  reader/phase/copy controls with 17 Windows-only skips, including both
  malformed/hung-provider regressions. The exact-code unit packet passed
  2,565 with 12 deselected. These results qualified the inherited-group fix,
  not deliberate group escape.
- Subsequent review found an escaped descendant could retain stdout past the
  watchdog. Native Linux run `40684489735` on
  `c5fbd22472d8a9d1e63f57350afd8ecb34b5faa6` reproduced both cases:
  a valid frame authorized after 12.026 seconds and malformed input refused
  only after 12.026 seconds. The escaped fixture had a hard lifetime armed
  before `setsid`, an after-escape readiness barrier and observed retirement.
  The new candidate shares one deadline across all reads and EOF, and replaces
  rejection's EOF drain with a private, prompt-free supervisor-status receipt
  in the existing copied launch directory. It preserves inherited-group
  custody and refuses escaped stdout without claiming cross-group retirement.
  Fresh native qualification and accepted review are still required.
- Candidate `936b49a87faadf20a02b58d29bf552466107e6e5` passed the native
  Linux escaped-stdout packet (64 passed, 17 Windows-only skips) and the
  Windows setup/Picker packet; macOS remained queued. Its unit packet passed
  2,565 with 12 deselected in 100.093 seconds. Review then found caller
  `noclobber` prevented writing the existing control file. A real regression
  reproduced refusal after 11.18 seconds; the writer now uses `>|` only for
  its own receipt, preserving caller options and suppressing raw path errors.
  The local corrected packet passed 78 with five native-POSIX skips,
  including actual control-write failure. This newer candidate still requires
  its own native packet and accepted review.
- Run `40685597320` later completed with a provider-side macOS runner-acquisition
  failure: no hosted runner acquired the job after repeated capacity attempts.
  Its cancelled macOS job executed no test code and supplies no macOS pass.
  The separately completed final run `40686894227` above is the native evidence;
  no old run was retried or relabeled as successful.
- Capability/rollback selection, canonical process-birth capture, backend
  custody and foreground terminal/signal behavior remain the next integration
  gates. Neither POSIX runtime waiter is removed, and no host installation,
  running service, session, release or rollout permission changed.

### 2026-10-28 - Dormant reader merged; finite POSIX consumer next

- #9321 merged as `a1e629c7d884cb1af9f0138fd330e3c20a6f718e`.
  Exact-head Copilot review `5680991863` on
  `0eadfa38a6589fe792301d3888bea98c8aad9c57` reported no open issues;
  its clean owner-authored `COMMENTED` shape satisfies repository policy.
  The overview's pending-native observation was superseded by the completed
  exact-head run below. All four review threads are resolved, and the fresh
  metadata-triggered required CI passed before managed completion. The
  authorized Maintainer exception covered only the additional review vote.
- Native run `40670087559` passed all three selected jobs: Linux Bash 5.2.21
  and stock macOS Bash 3.2.57 each passed 36 reader/copy controls with 10
  Windows-only skips; Windows passed nine copied-setup/creation controls
  and the independent uncertain-Picker-handoff control. The real nonblocking
  pipe, actual closed descriptors, caller-descriptor preservation and
  prompt-free refusal all passed without increasing their deadlines.
- The final no-actor unit packet passed 2,565 cases, 12 deselected, with
  coverage collected once. Real-process reader tests are selected explicitly,
  not admitted as fast structural guards. Earlier native failures above
  remain failures rather than being relabeled as passing qualification.
- Normal pull-forward attempted to replay the landed series and aborted.
  The complete local PR delta and actual squash delta had the same stable
  patch ID; retained recovery refs preserved the published tip. Advanced only
  that proven merged range, then completed normal reconciliation. The broader
  aggregate-squash repair remains tracked in #4130.
- Re-read the complete Plan and Validation Plan. Closed only the dormant
  reader/copy item. Continue finite current-runtime POSIX callback execution
  and native lifetime integration, preserving the existing seed/receipt
  owners, exact process birth, stdin, terminal signals and #5579's forwarding
  ownership. Production POSIX activation, release, installed acceptance,
  shared YAML standby and both live cycles remain open. No host installation,
  service or running session changed; this effort remains Active.

### 2026-10-28 - Reconstruction findings and cooperative standby direction

- The selected-host program cleanup and ordinary reconstruction exposed stale
  selection records, a disabled-task cold-start boundary and missing nested
  installer diagnostics. Created #9357/#9358 and retained existing
  uninstall/repair, diagnostics, readiness and maintenance owners in the
  [repair register](reconstruction-findings.md). Operational identifiers,
  backups and adopter-specific artifacts remain outside this public effort.
- Rebuilt runtimes and later serving checks are recovery evidence, not proof
  that every original failure is diagnosed. In particular, the Bridge
  disabled-task explanation is source-grounded but its original exception
  was not retained. The Index status/health differential still needs scope
  classification rather than assuming a broken opt-in gate.
- The operator clarified **run, but do no work**: tasks/registrars remain
  runnable, periodically check the KillSwitch, and resume loops/subscriptions
  after clearance. Updated the standing vision and explicit Plan/Validation
  gates. `Disable-ScheduledTask` is not that mechanism; current independent
  disablement must not be bypassed while implementing the replacement.
- The operator separately clarified that uninstall de-registers owned tasks,
  unlike normal in-place update. Recorded this as explicit Plan/Validation
  acceptance. The manual wipe retained registrations and is not evidence that
  a supported uninstaller violates that requirement.
- The fresh baseline had no active mux panes. This cleanup/reconstruction
  does not close same-process interactive mux survival, native standby,
  full launch parity or the two-cycle acceptance gates. The effort stays Active.
- #9363 merged after exact-head Copilot approval and required green CI.
  Re-read the Plan and Validation Plan after merge: the repair register and
  lifecycle clarification are durable, but their implementation and live
  acceptance remain open. Next, establish an actual current-release muxed
  witness before counting any further removal/reconstruction as session
  survival evidence; retain the existing maintenance and implementation owners.

### 2026-10-27 - Dormant copied POSIX reply reader

- Added the source-owned Bash frame reader and its normal installer,
  publication and uninstall inventory entries. The launch-owned asset copier
  now supports the complete POSIX normalized setup set without activating it.
  The [architecture contract](../../../plugins/agent-worktrees/docs/architecture.md)
  owns the reader's outputs and refusal behavior; no environment is applied
  and no backend is launched merely by reading a frame.
- Real Bash controls exercise literal data, empty replacement versus inherited
  environment, malformed frames, stale-output clearing and prompt-free
  diagnostics. Copy controls preserve the complete asset set outside removable
  roots and refuse missing-reader publication. Git Bash framing is not native
  POSIX terminal or installation-survival evidence.
- Continue with bounded current-runtime callback execution, exact process
  birth and foreground terminal custody before activating setup/backend
  lifetime ownership. Both production POSIX Python waiters, #5579 forwarding
  ownership and the parent live acceptance remain open.

### 2026-10-28 - Final-read error discrimination

- An actual closed stdin after the complete marker reproduced two failures:
  the reader accepted Bash's status-1 I/O error with nounset off, and aborted
  on an unbound probe variable with nounset on. Both controls now require the
  normal refusal and cleared outputs, not an injected return status.
- The final probe uses one finite Bash subshell to capture only read
  diagnostics, requiring status 1, empty trailing data and no diagnostics.
  Reply fields never pass through command substitution; no external parser,
  resident helper, environment application or backend execution is added.
- The focused reader/provider/copied-setup packet passed 87 cases with one
  native-POSIX skip on Windows. The separate native fixture prewrites a full
  frame into a nonblocking pipe, keeps its writer alive and writes later data;
  actual Linux and stock macOS Bash acceptance remain required before merging.
  This still does not activate a production POSIX consumer or close callback,
  exact-birth, terminal or installation-survival gates.
- Native run 40665465954 passed the real nonblocking-pipe control on Linux and
  stock macOS Bash, but both closed-stdin controls hit their unchanged
  15-second deadline. Refuse closed stdin before command substitution can reuse
  descriptor 0 for its capture pipe. The existing Windows terminal/mux packet
  passed; the failed native reader packet is not complete qualification.
- Actual early closed-descriptor controls also reproduced raw parser stderr
  before the generic refusal at three frame positions. Suppress the internal
  parser's stderr while retaining its refusal status and the final probe's own
  diagnostic capture. Exact-source native qualification remains required.
- Run 40666570855 still exposed the closed-stdin hang on both native POSIX
  hosts: Bash can optimize a descriptor-0-to-itself redirection without
  validating it. The precheck now duplicates stdin to a distinct temporary
  descriptor for the single builtin, restoring any caller descriptor
  afterward; a real retained-descriptor control covers that preservation.

### 2026-10-27 - Literal POSIX preparation transport prerequisite

- #9318 merged as `b4d1c753b6f7645f225a3c57f0af8caae24c50b3` after
  exact-head Copilot approval with no findings and required green CI.
  The combined finite-provider/native-adapter packet passed 59 controls;
  the required unit packet passed 2,521 with 12 deselected and coverage once.
  This is merged source, not a released or installed capability claim.
- Added a provider-first NUL-delimited preparation reply to the existing finite
  seed owner, with runtime-local read-only capability discovery and unchanged
  default JSON. The [architecture contract](../../../plugins/agent-worktrees/docs/architecture.md)
  owns field order, completion, environment replacement and unknown-outcome
  semantics; the provider never starts or waits for a backend.
- Focused controls cover literal fields and a real Bash reader without `eval`,
  invalid-format/callback refusal before effects, and retained seed custody
  after an unrepresentable reply. This prerequisite does not activate a copied
  POSIX setup or remove either production Python waiter. Native POSIX process,
  terminal, installer-removal and same-process acceptance remain open, as do
  #5579's separately owned forwarding and whole-chain process-count outcomes.
- Re-read this effort's Plan and Validation Plan after merge. The next stretch
  is the copied POSIX setup/native callback consumer, preserving real child
  custody, foreground terminal signals, stdin, one-time seed effects and quiet
  current-runtime return handling. No live removal is admitted by this source
  prerequisite or by an expired maintenance grant; the effort remains Active.

### 2026-10-27 - Finite native prerequisite merged; live survival stays open

- #9253 merged as `484cdc64bf2dcac44d16d463c31517b00fb6d71c`.
  The [canonical source record](<../source-managed-runtime-installs/journal/2026/10.27 native-runtime-removal.md>)
  retains actual-head review, required CI, 2,503 admitted units and the
  uninstrumented macOS entrypoint/resource proof. No skipped Windows control
  becomes positive native Windows qualification.
- The last recorded original-session admission still refused its rooted
  legacy runtime wrapper. The merged source does not close that migration,
  either real installation cycle, protected transport, per-worktree native
  launch integration, YAML standby or final lifecycle delivery. Continue the
  existing launch/removal owners and obtain the applicable native-action
  approval before another live experiment; this effort remains Active.

### 2026-10-26 - Preparatory review corrections

- The next review confirmed those five repairs and found connected consumers:
  same-owner reconnect must retain exit evidence, companions must resolve their
  pinned pane in a multi-pane mapping, and the copied-shell end must preserve
  the launch-correlated `pane_exited` mark. Added bounded current-installed-owner
  logging with separate uncertain diagnostic outcome, plus focused regressions;
  no captured runtime invocation or PATH fallback was restored.
  The combined corrected packet passed 107 tests with two platform skips.
  An earlier native end was unconfirmed; its isolated diagnostic controls
  and subsequent combined run passed under the unchanged callback deadline,
  without claiming a proven cause for that earlier result. The observer
  shutdown fixture now separates bounded dependency initialization from its
  unchanged four-second liveness/exit checks; it is not cold-start qualification.
- Opened #9150 through managed publication. Its first required unit lane
  passed, but the module-size guard caught growth in `sessions.py`; extracted
  unchanged prompt encoding into the existing pane-argument transport owner
  rather than widening its shrink-only ceiling.
- Review identified concrete lifecycle gaps. The candidate now uses strict
  snapshot-fenced starts, retains concurrent handoff CLI panes until their
  individual exits, fences tombstones and read-only duplicate exits, refuses
  absent-state end confirmation, and publishes admitted observations to an
  existing monitor without starting one.
- Narrow registry/reconciliation controls passed 158 tests before the final
  creation/reattach multi-pane controls were added. Final serial packets passed
  182 engine tests and 223 Manager tests with two explicit platform skips.
  Concurrent native runs had timeout failures; they are not passing evidence.
  Fixed overly broad subprocess patching, kept optional absent-install logging
  outside the seed-transport fixture, and isolated UI imports in worker-custody
  units that already replace the UI runner. No production or test deadline was
  increased. The shrink-only size guard and touched Python lint pass.
  The PR is not merged and none of this is installed acceptance.

### 2026-10-26 - Preparatory copied-shell candidate

- #9108 merged the exact-active-set migration contract; re-read the parent
  plan and retained every installation-survival and native-session obligation.
- Prepared canonical launch-owned shell copying and version-1 current-runtime
  lifecycle admission for tracked Manager and engine mux consumers. Preserved
  mixed-version legacy construction, same-executable reattachment identity,
  native incarnation fences, quiet unavailable handlers and Windows containment.
- The final targeted packet passed 30 engine launch/copy contract tests and
  141 Manager tests with four explicit platform skips. Both changed Bash
  scripts pass separate syntax checks; touched Python lint, PowerShell parsing
  and the runtime install-contract check also passed.
- Native subprocess controls exercise real copied shells with a prepared
  synthetic runtime. They do not qualify a complete installed dependency
  closure, real Copilot/TTY, Linux execution on this Windows host or an actual
  uninstall/reinstall. Cold/warm composed launch latency remains unmeasured;
  the five-second callback deadline is not a measured performance budget.
- Updated the baseline/candidate distinction in [launch-contract.md](launch-contract.md).
  The next source slice removes both runtime-Python seed/admission waiters
  while preserving custody, execution correlation and fsmonitor teardown.
  No selected-host session, service, installation or banner was changed;
  legacy migration stays blocked until the full replacement launch qualifies.

### 2026-10-26 - Live charter reviewed; runtime coupling confirmed

- #9074 merged as `bad5db98a52f844d614832b43b1c0cc310dbf4f1`
  after exact-head Copilot approval with no findings and green required CI.
  Managed completion used the normal Maintainer CODEOWNER-review exception;
  no CI gate was bypassed. Re-read the parent plan: the effort remains Active.
- Read-only live process ancestry and the refreshed source trace identify
  nested `launch_seed_exec` waiters below the existing Windows pane wrapper.
  Preserve those ancestors rather than terminate them to manufacture removal.
  The launch-contract inventory now includes both seed/setup boundaries.
- Normal admitted promotion run `40308349413` failed artifact construction:
  its pinned helper rejected `setup.py`. The existing source/helper-coherence
  repair #9061 owns that failure class; sent the exact normal-lane evidence
  there rather than opening a competing implementation. A later duplicate
  admission refusal is not the underlying build failure or permission to retry.
- A first bounded backup attempt did not complete and is not accepted evidence.
  The revised task-specific backup has native Windows WAL/restore and
  whole-command synthetic tests, explicit reconstructible-runtime exclusions,
  and progress/entry journaling. Live backup acceptance remains pending.
  No host services or installations have been stopped or changed.

### 2026-10-26 - Selected live-host round trip authorized

- The operator selected one live Windows test host and explicitly authorized
  audit, state separation, backups, careful uninstall/reinstall, re-release and
  necessary Main-promotion repairs until a real clean-middle round trip works.
  Preserve SSH-Gateway connectivity and muxed Copilot; no other host is covered.
- Added the generic live-roundtrip charter and validation obligations for
  review before effects. Read-only preflight reached the declared host through
  its existing Gateway forward; the remote shell is not elevated.
- No host files, services, installations or maintenance banner have been
  changed. The supported Manager uninstall prerequisite is source-merged;
  the complete parent process-survival and maintenance-standby gates remain open.

### 2026-10-24 - Canonical proposal

- Operator asked to resume the installation-survival proposal as a proper
  effort and confirmed its slug.
- Native effort-adoption probe passed. The source repository owns this generic
  effort; no personal state or host identity is part of the public plan.
- Opened #8669 after deduplication. Retained #5579, #8410 and #8312 ownership.
- Prior Linux prototype evidence is feasibility only. No production changes,
  host uninstall, installed update or new lifecycle service were performed.

### 2026-10-24 - Supported Manager uninstall selected

- The original proposal merged in #8675; the installation-survival effort
  remains active.
- A later real-installation preflight could not establish a supported Manager
  uninstall entrypoint. It stopped before interactive launch or removal and
  cleaned its owned container; no manual deletion was reported as uninstall.
- The operator chose a supported product uninstaller first rather than the
  test-only removal alternative. Opened #8864 after deduplication and retained
  this as a prerequisite of the same canonical effort.
- This change proposes the bounded removal/compatibility/validation contract
  for review before implementation. No uninstaller, runtime capability,
  process-survival acceptance or host rollout has been implemented by it.

### 2026-10-24 - Linux uninstall prerequisite

- The proposal and standing-intent amendment merged in #8675. The effort is
  active; the complete installation-survival contract remains open.
- A real disposable installation reproduced #8410: Bash rejected the
  script-scope `local` declaration during tool-binstub removal. The bounded
  repair removes that declaration without changing uninstall policy.
- Validated code snapshot `806bccf01f793dbd9eaf73dad513e9f286142bde` through
  native canonical source materialization in two fresh Linux clean rooms.
  The real-branch regression executed with one pass and no skips. Actual
  Worktrees and Manager installations passed; the complete Worktrees
  uninstall exited zero and removed its previously inventoried versions,
  Linux binstub, update helpers and deploy manifest.
- Labelled disposable project configuration, session JSON and tmux
  configuration retained identical hashes. Manager remained unchanged.
  Shared selectors, runtime resolvers, metadata and Windows-specific shims
  remain explicitly outside a complete-root-erasure claim. Both owned
  containers were removed; no host installation or service was changed.
- This evidence qualifies only the Linux uninstall prerequisite. Manager
  teardown, reinstall, interactive Copilot and TTY continuity, handler
  adoption, live update and native Windows survival remain unqualified.
  The native Windows launch baseline is still failing; no fixture-only
  success or timeout/containment relaxation closes that gate.

### 2026-10-24 - Refreshed Linux prerequisite validation

- After the upstream update, repeated the same packet at exact code snapshot
  `7211b0ad7a60b3a72ade8f18962b8b5281f5f385`: one regression pass without
  skips, both real installs successful, actual Worktrees uninstall exit zero,
  unchanged labelled durable-state hashes and complete owned-container cleanup.
- Preserved the separately merged Manager uninstall decision/contract while
  resolving only the effort documentation overlap. The Bash repair is unchanged.
  Supported Manager uninstall, actual interactive survival and the parent
  effort's remaining gates are not completed by this prerequisite.

### 2026-10-24 - Reviewed intent and initial consumer trace

- #8675 merged as `32b43c74e7e0f35a947eb605f8ef5ea83ad5b718` after an
  exact-head Copilot approval with no findings and required green CI. Managed
  completion used the repository-authorized exception for an additional
  CODEOWNER vote; it did not waive review findings or required CI.
- Reconciled the terminal PR through `pr-status` and pulled the source
  worktree forward with the normal guarded sync. No manual rebase bypass.
- Re-read the canonical plan after merge. Implementation and native acceptance
  remain open; a merged proposal is not completion.
- Began the production consumer trace in [launch-contract.md](launch-contract.md).
  Found version-pinned Manager end callbacks, installed wrapper paths and a
  distinct agent-worktrees handoff/new-window consumer; copying one script
  alone would not migrate the contract.
- Announced the bounded integration boundary on #5579. A separate cleanup
  rollout owner confirmed no wrapper/lifecycle edits and no custody conflict;
  that does not transfer #5579's broader forwarding-removal programme.
- Assigned one evidence-only Linux worker to the pinned real-installation
  baseline. No host uninstall/update, repository edits or credentials are
  delegated; a concrete setup/uninstall failure is a result, not a success.

### 2026-10-24 - Literal baseline and native prerequisites

- Pinned Linux baseline at the reviewed source completed real installations
  of both products. The actual Worktrees uninstall failed with exit 1 on its
  top-level `local stub_path`, reproducing #8410. Both owned containers were
  removed; no host installation or shared Docker service was changed.
- That baseline did not launch tmux or Copilot and did not reach Manager
  uninstall, reinstall, state-preservation or handler-adoption acceptance.
  It is a prerequisite failure, not a survival pass.
- The selected native Windows wrapper/argv baseline initially exposed a
  parser-test timeout. Strict error handling found that the fixture included
  unrelated undefined hosting-admission calls and silently masked a missing
  Newtonsoft type on PowerShell 5.1. A local prerequisite repair uses the
  built-in string-preserving JSON reader and strict parser-only fixtures.
- The repaired selected native matrix passed 57 tests with two opt-in skips;
  both PowerShell 5.1 and 7 exercised literal strings and malformed-command
  refusal. This is local working-source evidence, not a reviewed/released fix.
- Enabling the skipped real PSMux consumers exposed fixture dependency
  coupling and then an unresolved delivery failure: a pane handoff may be
  consumed while the controlled child's output never appears within the
  existing budget, or the session exits immediately. A direct native child
  control remains live after its creating client exits.
- A temporary copied-wrapper trace reached wrapper entry with the correct
  native command and arguments. Disabling the pane Job in an isolated
  diagnostic did not resolve the failure; containment remains required and
  enabled. Waiting for exact pane teardown and making the fixture child
  noninteractive did not establish a passing delivery result either.
- Temporary wrapper tracing and the diagnostic Job override were removed.
  The native delivery failure must be diagnosed before calling this baseline
  green. No deadline was raised and no application session was stopped.
- The Linux worker returned a minimal uninstall fix and a proposed real-branch
  regression/rerun plan. Neither has been applied or qualified yet. The
  production copied-wrapper/current-handler migration remains outstanding.

### 2026-10-24 - Longer-running native diagnostic

- Operator direction:

  > Perhaps just run the tests unbounded and see what happens right now

- Temporarily removed the native fixture's short subprocess, output and
  teardown waits, while retaining real PSMux, both production producers,
  exact process identities, observation and kernel containment.
- The first diagnostic still hit the runner's separate 30-second item
  watchdog. Its stack was waiting for the PowerShell producer's stdout to
  close, before that cycle's PSMux launch.
- The contained runner requires positive budgets. With diagnostic-only
  `--test-timeout 1200 --timeout 1500 --plugin-timeout 1800`, all three native
  cases passed: both producers completed two cycles and the direct control
  passed. Pytest reported three passes, no skips and 53 deselections in
  80.63 seconds; the complete command took 97.83 seconds.
- This establishes eventual delivery for that diagnostic, not a pass under
  the ordinary budgets or proof that every earlier failure has one cause.
  Separate setup, delivery and teardown timings before changing a permanent
  test budget. Interactive Copilot and actual installation survival remain
  open.
- Restored the temporary test edits exactly to the published candidate.
  No production deadline, process-containment policy or acceptance gate changed.

### 2026-10-24 - Scoped native budgets and uninstall-contract preflight

- Phase timing did not justify calling every failure a timeout: PSMux 3.3.5
  also returned an immediate error followed by an absent server. An isolated,
  digest-verified 3.3.6 comparison completed both cycles without changing the
  host installation.
- Observed native readiness reached 23.344 seconds. A later run separately
  measured file creation at 13.810 seconds and its pane query at 0.172 seconds,
  confirming that the former eight-second fixture limit was too short.
  Pane teardown remained below 1.2 seconds and descendant cleanup below
  0.05 seconds.
- Replaced temporary unbounded instrumentation with scoped, finite integration
  limits: 180 seconds per opt-in two-cycle case and 45 seconds for its
  PowerShell preparation/readiness. Ordinary unit defaults and existing
  client, query, teardown and containment limits remain unchanged.
  The normal runner passed all eight selected controls in 86.70 seconds;
  both workflow contract tests also passed.
- The manual Windows lane now pins test-tool PSMux 3.3.6. This is neither an
  operator-host upgrade nor a new production compatibility-floor policy.
- A fresh Linux preflight at published source
  `0e5d68c82bb8a7f810f5b60342c0e86a7a8995a5` borrowed credentials through
  the normal provider, installed both real products and the missing tmux
  prerequisite, and registered a synthetic local project through the actual
  CLI. It did not establish interactive Copilot entitlement or launch a session.
- The baseline stopped because no supported Manager uninstall entrypoint was
  established from its installed help and pinned product entrypoints. No
  manual file deletion was substituted for uninstall. The exact owned
  container was removed and the shared Docker service left running.
  Resolve that product contract before claiming a real two-product survival
  baseline; the copied-wrapper/current-handler implementation is still open.

### 2026-10-25 - Prerequisites merged and implementation resumed

- #8763 merged as `cf5bfdea83164a21e717591f6e2055bbce8ebbb6` after
  exact-head approval, green required CI and final changed-dependency
  qualification at `f00b06be11bb1e3f639be5db35cbc3573337e235`.
  The real Linux regression passed without skips; both actual installs
  passed, Worktrees uninstall returned zero and labelled durable-state hashes
  were unchanged. Both owned disposable containers were removed.
- This closes the bounded #8410 prerequisite, not complete Worktrees-root
  erasure or same-process installation survival. Shared selectors, resolvers
  and cross-platform shims remain inventoried rather than claimed removed.
- #8871 merged the supported Manager uninstall contract. Implementation now
  targets the existing installer with read-only preview, attributable artifact
  removal, existing publication/retention custody and an external interpreter
  boundary. Older layouts without sufficient ownership evidence must refuse;
  the installer must not rebuild a target merely to remove it.
- The scoped native argument-delivery candidate passed its local eight-case
  packet and the fresh Windows lane at `5470775c0ec8f940d50e3b6e82b08252790eb424`.
  Those results supersede the earlier unresolved diagnostic for that narrow
  candidate, not interactive Copilot or installation-removal acceptance.
- Next: qualify and land the native argument-delivery prerequisite, implement
  and qualify supported Manager removal, then resume the same real interactive
  two-product baseline and production copied-wrapper/current-handler work.
  The parent effort, release and approved installed validation remain open.

### 2026-10-25 - Native argument prerequisite merged

- #8906 merged as `bea4d7bb70c2ec727b2f38caeff9ec84eaca26c2` after
  exact-head approval without findings and required green CI. The fresh
  Windows run `40173234492` exercised published source
  `cbd2966ed527b29cc37579685e8e6d2b0a4993d6`: all three real PSMux
  controls passed without skips, including both file-based producers and
  owned teardown. The rebased ordinary pane packet also passed 53 tests;
  both workflow-contract checks and the install-contract guard passed.
- Re-read the parent plan after merge. This closes only the literal Windows
  argument-delivery prerequisite. It does not implement the copied shell,
  current start/end handlers, actual two-product removal, update survival,
  release or approved selected-host validation.
- The first supported Manager uninstall implementation is under integration
  review. Its focused ownership/custody controls pass, but real installed
  removal on Linux/Windows, mixed-version controls and full assembly
  validation are still required before returning to the interactive baseline.

### 2026-10-25 - Priority resumed with maintenance-window intent

- The operator prioritized installation-independent sessions and added
  local-machine maintenance notification, native standby and resumption after
  the YAML window clears. The literal follow-up is in the inception transcript.
- Reconciled the canonical source workspace through guarded sync, preserving
  the committed parent journal. Found #9036's already merged JSON-only conduct
  guidance and #9042's reviewed disposable-runtime/shared-YAML vision and plan.
  Reuse those owners and contracts; do not create another banner or protocol.
- The expanded Plan and Validation Plan connect actual process survival to
  tool-independent banner reading, preserved obligations, sparse native standby
  and safe capability revalidation after clearance. Normal guidance delivery
  and native behavior still need proof; reviewed intent alone is not acceptance.
- Supported Manager uninstall remains a prerequisite. Its three review fixes
  are committed locally but unpublished; the Windows synthetic-payload fixture
  timeout and revised native qualification remain unresolved. This update
  neither declares them passing nor authorizes host maintenance or rollout.

### 2026-10-24 - Uninstall contract reviewed

- #8871 merged after an exact-head Copilot approval with no open issues and
  successful required CI. Managed completion used the live-confirmed
  Maintainer exception for an additional CODEOWNER approval; no CI or reviewer
  finding was bypassed.
- Re-read the parent plan and prerequisite after merge. #8864 remains open:
  the contract is reviewed, but implementation, actual Linux/Windows removal
  and the parent installation-survival acceptance are still outstanding.
- Next, inventory the concrete installer/ownership/lifecycle seams and
  implement the smallest supported removal operation. No operator-host
  uninstall, live-session intervention or rollout follows from plan approval.

### 2026-10-26 - Maintenance integration intent merged

- #9045 merged as `2687e35fa9216bb93c0907d4547d5f1b264e8ae2` after
  exact-head Copilot approval with no findings and required green CI. The
  managed merge used the repository-authorized exception for an additional
  CODEOWNER vote, not a waiver of findings or status checks.
- Re-read the canonical Plan and Validation Plan after guarded pull-forward.
  The maintenance extension is reviewed intent only: normal YAML guidance
  delivery, native standby and safe resumption remain unchecked, as do the
  original actual two-product session-survival gates.
- Resume the supported Manager uninstall prerequisite first. Preserve its
  three review fixes while reconciling current native qualification portfolios;
  resolve the real test failure and rerun changed-head native acceptance before
  returning to production wrapper/current-handler migration.

### 2026-10-26 - Supported Manager uninstall source merged

- #8955 merged as `c2cc00e92b47f1e9036b3ae4d9e8f329ee796309` after
  exact-head Copilot approval, resolution of every review thread and required
  green CI. The metadata-triggered CI run also passed before managed completion;
  the repository-authorized additional-CODEOWNER exception did not waive CI.
- Qualified source `0d78da44bffbb5120fc45d4b1e0fcefc1d21f9d6` passed
  203 affected Windows tests with three POSIX-only skips, all 206 Linux tests
  without skips, and actual installed daemon/configuration-list/removal/reinstall
  flows on both platforms. The fresh Windows lane was `40292624279`; the
  Linux native test passed in 58.20 seconds. Artifact and installed/source
  identities, durable-state bytes and exact disposable cleanup were verified.
- This closes the supported-uninstaller source prerequisite only. Normal
  containing release, approved installed acceptance, actual same-process
  interactive two-product survival, copied wrappers/current handlers and YAML
  maintenance standby remain open.
- Re-read the canonical Plan and Validation Plan. Return to the real
  interactive baseline and current launch-contract inventory, preserving
  #5579's forwarding ownership. Do not treat this merged prerequisite as
  completion or retire the continuing parent worktree.

### 2026-10-26 - Real interactive baseline and read-only probe repair

- The supported Manager uninstaller from #8955 is merged. A disposable ARM64
  Linux container installed Manager and Worktrees non-editably at merged
  source `e4dcb7226c2b928d53f4cd0ba184b1f1619d7ee5`, then ran the actual
  installed Manager launch script with a terminal attachment. The observed
  mux server, installed pane shell and native Copilot remained live while a
  literal no-tools witness answered the initial readiness prompt.
- The uninstall preview refused before reaching quiescence. The launch-time
  maintenance policy probe had created three bytecode files and their cache
  directory inside the sealed Manager payload. Original file bytes and
  inventory identities were unchanged. Do not weaken the inventory check,
  delete the cache manually, or label that refusal a survival pass.
- The bounded repair disables bytecode writes in the probe before its first
  Manager import. Four subprocess controls exercise absent, enabled, disabled
  and malformed policy twice with the runner's bytecode environment ignored.
  They preserve payload bytes and the existing exit codes and diagnostics.
- Extended native uninstall acceptance executes the real installed probe
  with an external base interpreter and no bytecode-disable flag, compares
  the sealed slot, and continues through actual removal and reinstall.
  The native Windows cycle passed, including daemon start/stop, configuration
  listing, unchanged durable data and idempotent repeated removal; the
  combined bounded assertion run passed 32 tests.
- The initial source-install clean-room failed because its base image lacked
  the C compiler required by the native process helper. Providing the
  explicitly recorded build prerequisites inside that disposable fixture
  allowed the real installation. That is not pristine-image acceptance.
- Actual removal of both systems around the interactive session, a copied
  installation-independent shell, replacement-handler exit, the full suite,
  shared YAML standby, native Windows survival and released/installed
  acceptance remain open. The next slice resumes that same real-removal
  baseline after the read-only probe repair.

### 2026-10-26 - Actual Linux round trip and whole-suite prerequisites

- #9075 merged as `144183e71d55a22088b3d1082ac2dac155908190`.
  A new disposable installation at that exact merged source passed the real
  read-only launch probe, authenticated metadata-daemon shutdown, supported
  Manager removal and Worktrees removal, followed by actual reinstall.
  The same five mux, wrapper, launch and Copilot process incarnations survived.
  The native assistant transcript records responses before removal, while both
  primary commands and installed generations were absent, and after reinstall.
- Six durable files remained byte-identical. The project registry changed
  only its normal registration timestamp; all other parsed registry data
  remained identical. Worktrees retained generated helpers and snapshots, and
  the resident launch interpreter remained alive. This is a bounded two-product,
  same-source Linux baseline, not whole-suite removal, copied-wrapper,
  replacement-generation exit, native Windows or installed-release acceptance.
- The same surviving session then received all 13 declared runtime adopters
  plus Manager from that merged source. Every selected runtime identified the
  exact source commit and had completion evidence. The same five process
  incarnations remained unchanged, and the native transcript recorded another
  no-tools assistant response after installation.
- Actual supported Index uninstall returned success and removed its command,
  but retained generations, snapshots and selectors. Other prerequisite
  source inspections found missing legacy bootstrap uninstall actions and
  default state-erasing removal paths. These are removal gaps, not a reason
  to substitute manual directory deletion or claim the middle state clean.
- The bounded Logger repair removes only declared reconstructible children
  under its existing install transaction, preserving configuration, digests,
  databases, session state and unknown root content. It validates deployment
  or in-root stamped ownership before removing tasks or commands; foreign,
  malformed, missing and externally redirected ownership refuses explicitly.
  Native Windows deployed/stamped ownership and idempotence controls pass,
  as do the affected existing Logger installer tests and isolated POSIX
  controls. Whole-suite native removal remains the next acceptance gate.
- #1886 retains the symmetric harness-wide declaration, shared-reference and
  confirmed plan/apply contract. Per-product prerequisites do not replace that
  owner. Containers' runtime-hosted SSH broker and Machines' admission boundary
  require explicit retention/quiescence handling before their code can be
  removed; transport, credentials and external resources are not inferred to
  be disposable from a product name.
- Keep this effort Active. Continue the supported per-product removal
  prerequisites, then the real all-absent/reinstall witness around this same
  session. The protected transport closure, launch-owned shell/current hooks,
  shared YAML maintenance admission and native standby remain unresolved.
- The prerequisite review exposed and corrected literal child-name matching,
  same-product foreign ownership, active Logger syncs, persistent snapshot
  coordination and retry authentication after partial cleanup. Native
  task/process/custody controls and isolated Linux integration cover these
  boundaries. This adds no whole-harness reference-accounting or abrupt-death
  recovery claim; the real all-absent witness remains open.

### 2026-10-26 - Removal foundation merged, portfolio integration continues

- #9087 merged as `6ab06aa912e5b243ca6f8cebd5a3b489f04fc2bf` after
  current-head review without open findings and required green CI. The
  [source qualification record](<../source-managed-runtime-installs/journal/2026/10.26 runtime-removal.md>)
  separates the actual Windows/Linux packets from live-session acceptance.
- Selective native cleanup, finite child settlement and the Logger/MCP
  prerequisites now preserve default durable/unknown state and coordination.
  Root aliases, Windows filesystem casing, active consumers, historical
  registrations and retry ownership were corrected rather than represented
  as successful removal. The container terminal-process assertion has a
  bounded portability follow-up under this effort.
- Re-read the Plan and Validation Plan after merge. Actual all-absent removal
  and reinstall around the same session remains open, as do complete default
  adapter coverage, protected SSH/Gateway closure, copied wrappers/current
  boundary handlers, native Windows survival, YAML standby and released
  installed acceptance. No checkbox for those gates changes here.
- Continue the remaining adapter integration through the existing ownership
  boundaries, then return to the preserved live baseline. This effort remains
  Active; a merged removal prerequisite does not retire its responsibility.

### 2026-10-27 - Registration and retirement race corrections

- The copied-shell review confirmed reconnect and pinned-companion fixes, but
  also exposed delayed creation reviving an already-ended pane and pane exit
  reviving an explicitly retired whole mapping. These remain blockers until
  the corrected implementation and regression packet pass review.
- Reconnect now retains dead CLI fences, refuses same-incarnation tombstones,
  and rejects creation of an ended pane, including after snapshot-CAS retries.
  Pane-owned exit records evidence without undoing whole-mapping retirement.
- The combined registration/registry/companion packet passed 208 tests with
  two platform skips. A stale single-CLI assertion was corrected. Native
  relative-root fixture preparation is now explicit, separate from unchanged
  ten-second rendezvous and fifteen-second refusal limits; the isolated six
  controls also passed. Earlier startup timeouts remain recorded, not claimed
  as cold-start latency qualification. Lint and the module-size gate passed.
- No live installation, session or service was changed. The effort remains
  Active; these corrections do not qualify the remaining runtime waiters or
  the actual clean-middle reinstall contract.

### 2026-10-27 - Startup validation and creation evidence

- The next review overview raised failed startup validation and delayed creation.
  Both copied shells had registered before validating token/prompt transport or
  publishing its provisional receipt, without retiring that registration on a
  pre-dispatch failure. A fenced cancellation now retires only that pane without
  inventing an observed child exit. Start remains before the receipt: its real
  consumer checks pane liveness during grace, so delaying start after receipt
  publication could falsely admit a child while the handler was still pending.
  Windows invocation errors produce an explicit failure and reach pane teardown.
- Every creation callback now requires affirmative current native CLI identity,
  liveness and path evidence, not just callbacks replacing a conflicting entry.
  Existing same-incarnation tombstones and dead pane fences still refuse first.
- These are further preparatory source corrections, not deployment or
  installation-survival evidence. Native failure controls and the changed
  consumer packet remain the local validation gate before publication.
- The initial packet passed 111 tests with seven platform skips; adding the
  positive child-exit control exposed another five-second native callback
  timeout. A bounded diagnostic first passed both controls, then reproduced
  start/end timeouts during runtime imports. Import timings and timed stacks
  are now captured in the synthetic fixture without widening the production
  deadline. The timing failure remains open; a green retry is not its cause.
- The revised cancellation/creation/consumer/native packet passed 117 tests
  with seven platform skips. Actual Windows wrapper controls cover invalid
  prompt/token transport, blocked receipt preparation, native-locked receipt
  publication, missing-command failure 127 and preserved real child exit 7.
  Registry controls retain the predecessor and reject fabricated exit metadata
  on cancellation. Runtime import timing remains an explicit #8669
  qualification gap, not a basis for migrating live sessions.

### 2026-10-27 - Delayed start against a foreign tombstone

- The next exact-head overview identified another real race: an old start
  could replace a different incarnation's newer tombstone and recorded exit.
  Start now requires fresh native CLI/pane/path proof before that replacement,
  repeated after a snapshot-CAS conflict. A live foreign incumbent still
  refuses without replacement.
- Creation and start reuse one native-proof function in the existing discovery
  owner. Numeric incarnation timestamps do not grant takeover authority.
  Regression controls cover current, stale and unknown native evidence, plus
  an intervening tombstone change during the probe; bounded native subprocess
  controls exercise the real descendant-Job query path.
- Local validation and current-head review remain required before publication.
  The effort remains Active and no target installation or session was changed.
- The final shared-proof/discovery/registry/CLI/companion packet passed 197
  tests with two platform skips; lint, module size and changed-file whitespace
  passed. The Windows subprocess controls use an isolated native-protocol
  fixture, not a human mux or installation-removal witness. Initial failures
  were fixture wiring and an explicitly rejected stale successor revision;
  the CAS regression now asserts that its intervening mutation actually landed.

### 2026-10-27 - Equivalent worktree identity corrections

- The next review overview identified raw worktree-path comparisons in start,
  reconnect and discovery admission. Equivalent native paths now retain their
  logical owner, dead CLI fences, latest observed exit and native-discovery
  identity. Exact stored-snapshot comparisons remain unchanged for CAS.
- Regression controls cover existing `sub/..` paths, actual Windows case
  aliases, genuinely different paths, duplicate and subsequent exits, ended-pane
  creation refusal and an alias-only concurrent write invalidating a snapshot.
- Rebased onto current upstream while preserving its allocation/pair-completion
  changes and removal-foundation journal. The creation builder's duplicate
  seeded/unseeded call branches were folded without changing their arguments.
- The Bash deadline helper's finite 50-millisecond sleep-child checks are
  bounded to a boundary call, not a resident poller. Their overhead and native
  callback latency remain unqualified under #8669; no deadline was increased
  and no new persistent helper was introduced.
- Local tests and a fresh exact-head review remain required. No target session,
  service or installation has changed; the parent effort remains Active.
- The identity/CLI packet passed 167 tests and the registry/companion/native
  protocol packet passed another 155. Rebased creation validation exposed a
  Windows slash-format assertion and cold imports sharing a three-second lock
  readiness deadline. The assertions now compare native paths; the fixture
  separately prepares imports before its unchanged lock-acquisition and
  exclusion windows. These fixture changes do not alter production deadlines.
- The final rebased creation packet passed 72 tests. Two earlier attempts
  exhausted the runner's 300-second aggregate preparation budget; the final
  invocation allowed 600 seconds for preparation plus execution while retaining
  its 300-second test budget. It completed in 73.34 seconds, including cleanup.
  Python lint, module-size and whitespace checks passed.

### 2026-10-26 - Complete native-removal prerequisite merged

- #9143 merged as `883cdccb2cef63f131c371018aa2486aaf3e217a` after
  exact-head Copilot Comment review without open or inline findings and
  required green CI. All 21 review threads were resolved; the managed
  Maintainer exception covered only the additional CODEOWNER vote.
- The [qualification record](<../source-managed-runtime-installs/journal/2026/10.26 runtime-removal.md>)
  records supported finite adapters, original process admission, actual native
  writer forms, shared transaction custody, packaged closure, external Python
  selection and safety-option refusal. These are source prerequisites, not
  actual whole-suite installation absence or interactive survival.
- Re-read the Plan and Validation Plan after merge. No complete acceptance
  item changes. Return to the preserved original-session baseline through
  supported native removal/reinstall from clean merged source, with backups,
  exact original process identities and protected current/fresh transport
  closure first. A plugin update or Main promotion is not required for that
  direct source-driven attempt; actual effects still require applicable consent.
- Launch-owned wrappers/current boundary handlers, native Windows survival,
  maintenance-aware YAML standby and released installed acceptance remain
  open. This effort stays Active.

### 2026-10-27 - Copied-shell prerequisite merged; seed boundary continues

- #9150 merged as `a7680d0ecc7b387ca66110a590bce36a88b0d069` after
  exact-head review `5662297754` and green required checks. The review reported
  no inline code findings and correctly retained POSIX signal/terminal and
  containment qualification as an open concern. The maintainer assessment
  records that this preparatory merge is not a migration or survival witness.
- Re-read the complete Plan and Validation Plan. No broad implementation or
  native-acceptance checkbox is closed by this prerequisite: both deferred
  setup and final `--invoke` runtime waiters still remain. Installed source
  release, cold/warm timing, literal removal, native Copilot/TTY, standby and
  exact active-set restoration remain continuing gates.
- Pulled the owning worktree onto the confirmed squash merge, retaining the
  pre-sync recovery refs. Continue the seed/admission boundary in this same
  effort, outside #5579's retained front-door forwarding/shim ownership. No
  host installation, runtime update or session retirement was performed.

### 2026-10-27 - Finite seed preparation seam

- Separated the existing finite policy/argv/seed-claim preparation from
  `launch_seed_exec`'s process invocation and wait. The legacy caller still
  owns the same admission guard, spawn, finish/restore and wait sequence;
  no native adapter or capability is activated by this refactor.
- The native integration must reuse that policy, not reproduce it in shell.
  Preparation retains the claimed handoff until actual spawn and neither
  finishes it nor automatically restores an uncertain outcome. Existing
  execution correlation and stale native-hint removal remain in that owner.
- Both runtime waiters remain present until the native integration and its
  complete consumer/containment/terminal acceptance pass.
- The preparation, execution and attempt suites passed 29, 29 and 128 tests.
  The remaining seed-delivery suite timed out in a real PowerShell consumer:
  killing only its immediate subprocess left descendants holding capture pipes.
  Its test now uses the existing finite process-tree owner so a timeout yields
  diagnostics and cleanup instead of blocking the entire suite.
- Launch exclusion is explicitly per worktree and ends when its mux becomes
  discoverable. Same-worktree contenders join; different worktrees remain
  parallel. The native integration must not preserve the legacy admission-only
  wrapper's session-lifetime lock.
- The approved live validation is a two-cycle installation round trip: capture
  active native conversations, close legacy muxes, remove tools/services,
  reinstall and restore using the new launch path, then repeat removal and
  reinstall around those durable sessions. Manual cleanup covers only
  identified process/installation leftovers, never durable state or config.
  Backups, protected connectivity and exact restoration remain prerequisites.
- Current implementation focus moves to Phase 3's finite native seed/backend
  boundary. Earlier reproduction and full native-acceptance items remain open;
  this slice transition does not mark them complete.
- The internal finite backend provider now reuses existing execution and seed
  records without spawning or waiting for Copilot. Initial controls passed 15
  tests after correcting two test expectations for placement before literal
  `--`. Current-runtime CLI refusal and a dormant, dot-sourced Windows native
  adapter are being added; no production caller or capability is activated.
- Revalidating the complete 128-case seed-state file reached its 300-second
  suite budget. A focused launch/admission partition passed 93 tests in 217.77
  seconds with 35 pair-GC cases explicitly deselected. The earlier complete
  packet had passed all 128 before the shared request-validation extraction;
  neither timeout is relabeled as a green full-suite run.
- #5579 coordination was refreshed for the finite seed slice, preserving its
  forwarding/shim ownership and whole-chain acceptance. The absence of a peer
  response does not transfer custody.
- Current-completed-runtime refusal controls passed with the finite provider:
  20 tests. The first four-case Windows adapter run exposed real output loss
  for a consoleless child. Explicit redirected output handles and asynchronous
  byte forwarding now preserve original stdin as well; the expanded native
  packet passed all five cases, including exact stdin/stdout/stderr, literal
  argv, actual PID/birth and exit, definite start refusal and absent end.
- The adapter is dot-sourced into an existing lifetime owner, not another
  persistent shell. This is a dormant Windows provider-first slice: production
  callers, copied-asset integration, POSIX native adaptation, real terminal/
  Ctrl+C/Job acceptance, per-worktree mux-create fencing and both removal
  cycles remain open. No target installation or session changed.
- Review found two concrete adapter defects: a failed redirected sink could
  stop draining and block its child, and PowerShell's selected location could
  differ from the inherited process directory. Finite callbacks and backend
  start now set the selected filesystem location explicitly. Redirected streams
  use event-driven child/task waits; a failed destination drains to null without
  losing the actual exit and correlated return. No polling helper was added.
- The expanded Windows packet passed nine tests, including actual closed
  stdout and stderr sinks with output exceeding pipe capacity, and distinct
  PowerShell/OS-directory controls for both finite and backend execution.
  The launch-site census also classifies both adapter process starts as
  caller-contained; its source guard passes. These corrections do not activate
  callers or close native terminal/installation-survival acceptance.

### 2026-10-27 - Finite native provider prerequisite merged

- #9242 merged as `19b238586593af584381d0926b22a6abad096596` after
  exact-head Copilot approval `5663664248`, both fixed review threads resolved
  and required green CI. Managed completion used the authorized additional
  CODEOWNER-review exception, not a CI bypass.
- Re-read the canonical Plan and Validation Plan. Only the finite-provider
  groundwork milestone is complete: production consumers, both runtime
  waiters, per-worktree mux check/create fencing, native terminal/containment,
  maintenance standby, release and both live cycles remain open.
- Preserved recovery refs, then synchronized only the provider-confirmed merged
  range onto current `dev`. The next slice integrates the finite boundary into
  existing lifetime owners and copied assets; it must not replace the two
  Python waiters with two additional persistent PowerShell waiters.

### 2026-10-27 - Normalized Windows consumer integration in progress

- The candidate runs native-capable normalized PowerShell 7 setup directly
  from launch-owned storage. It reuses that existing host for finite outer
  admission/preparation and actual backend handle/wait ownership; no second
  resident shell replaces either removed Python waiter.
- Provider, copying, correlation and original adapter controls first passed
  87 tests. An expanded packet passed 88 but exposed four whole-consumer
  fixture encoding failures; capturing literal arguments in an explicit UTF-8
  file corrected those fixtures, and all four consumer controls then passed.
  A broad launch-regression selection hit runner time budgets and is not
  counted as green.
- Real windowless Windows terminal qualification is being added, including
  the actual pane kill-on-close Job. Its retained child-handle teardown
  control passes. The interrupt fixture still needs to exercise a real
  console input reader; a non-reading child cannot prove turn cancellation.
  Neither caller activation nor these partial controls qualify live migration.
- Use `/exit` and verify actual pane/backend exit for controlled migration.
  Single Ctrl+C, backend-owned rapid-repeat termination, attach loss and real
  child exit remain distinct. The latest literal direction is preserved in
  [the inception transcript](inception-transcript.md#operator---graceful-exit-and-repeated-interrupt-behavior).
- Batch/custom/old-host and POSIX parity, concurrent mux admission, actual
  installed runtime/terminal acceptance, release, standby and both live cycles
  remain open. No target installation or session changed.
- The production builder always includes `launch-command.ps1`; direct-setup
  fixtures alone would have missed that consumer. The candidate now relocates
  that existing wrapper with its sibling setup and keeps its existing
  same-host dot-source fast path, with typed correlation inserted inside the
  wrapper transport. It does not take over #5579's forwarding-layer work.
- The combined packet passed 101 tests and exposed a real outer-wrapper
  defect: after one interrupt, child survival and its exit code were preserved,
  but PowerShell cancellation skipped post-child handling. The candidate now
  copies the engine-owned console helper beside a capable canonical pane
  wrapper and protects that existing owner's child wait too. An old peer
  retains the legacy command; this new activation fence and the copied-pane
  correction are under native regression.
- The copied-pane fix passes the single-interrupt post-child diagnostic.
  Synthetic keyboard-reader controls proved unreliable, so the native
  wait-owner packet now delivers actual Windows control events through a
  bounded helper attached only to its owned ConPTY. All six direct/outer-pane
  controls and ten selected existing normalized-launch regressions passed
  together: 16 tests, 154 explicitly deselected. This validates console event
  handling, not physical keyboard delivery or a real mux/Copilot installation.
- Existing creation/resume coverage passed 15 tests. The 93-case seed-delivery
  run initially had 92 passes and one Windows raw-capture timeout at the
  admission-refusal boundary. Both coupled admission tests now use the existing
  finite process-tree capture owner with the same 15-second deadline. The full
  file then passed all 93 tests; this is not a claim that arbitrary cold
  startup latency was fixed. Real current-runtime/CLI integration and installed
  qualification remain the next gate, before publication or live migration.

### 2026-10-27 - Current-runtime consumer qualification

- The independent local production review found no significant issues and
  retained the unproved seeded cold-start, real Copilot/keyboard/mux and
  uninstall/reinstall gates. It is not a provider review or rollout receipt.
- Added a disposable source-attributed runtime/consumer lane using the existing
  canonical construction fixture. It does not replace the finite provider or
  current-runtime selector. The initial run timed out during construction,
  before a consumer assertion; it is not acceptance evidence. The diagnostic
  run retains the existing 300-second construction deadline with file-backed
  installer output and the construction portfolio's existing outer test budget.
  No production deadline was increased.
- The new lane initially separates no-seed backend dispatch, admission-only
  copied setup and foreign-root refusal from seeded/mux qualification.
  Publication, host installation and both live cycles remain pending.
- Stack diagnostics subsequently located the stall in retained local-source
  copying and repeated byte verification, not the governance lock. The
  consumer fixture now makes an independently committed runtime-only source
  payload, omitting test suites but retaining the real installer and policy.
  The full-source construction timed out at 300 seconds on this worker; no
  full-source latency improvement or successful consumer outcome is implied.
- The runtime-only source also reached the 300-second construction test
  deadline before consumer dispatch. Read-only inspection found exactly the
  expected 16 local inputs and no inherited UV project override. Since stack
  samples showed source-copy/hash progress, the diagnostic construction test
  now allows 600 seconds with a 900-second item budget to measure the eventual
  outcome. Production constructor, child, policy and native callback deadlines
  are unchanged. Neither earlier timeout becomes green retrospectively.
- The measured run constructed and selected the real runtime successfully.
  Its following metadata probe failed because this new test passed `-c` to
  the module-only retention bootstrap. Metadata and fixture initialization
  now use the actual installed interpreter directly; production native
  callbacks still use their unmodified retained `-m` bootstrap contract.
  The backend fixture waits for its real spawn receipt before returning, so
  a fixture's immediate exit cannot race the provider's PID/birth admission.
  Consumer assertions have not yet passed.
- The next packet reached consumer dispatch: foreign-root refusal passed;
  direct backend capture timed out at 30 seconds; copied setup propagated
  exit 7 but encountered an ambient compatibility shim during optional
  project-name lookup. The fixture now binds its actual selected runtime,
  excludes host command shims from the driver's PATH, and uses the existing
  finite process-tree capture owner at the same 30-second deadline. Timeout
  diagnostics retain the actual execution observations rather than hiding
  the boundary behind a capture exception. Both consumer cases remain red
  until the rerun verifies them.
- Corrected a test-only capture-helper API mismatch. With direct bootstrap
  diagnostics both backends then received the literal arguments and returned
  exit 7; copied setup passed, but direct backend lacked its final receipt.
  The preliminary provider invocation made that warm-path evidence, not cold
  acceptance, and has been removed.
- A fresh-interpreter sample showed the finite module importing preparation
  and status-daemon dependencies in 3.674 seconds. Return-only callbacks now
  defer those imports to preparation or actual seed handling; a second local
  sample took 0.509 seconds and did not load either subsystem. These are local
  samples, not a cross-run latency guarantee. A subprocess import-graph
  regression and the actual cold consumer packet are under validation with
  the original five-second native phase deadline unchanged.
- The lean-callback packet passed all 30 cases: 27 finite-provider/import
  controls and three real-runtime no-seed consumer/foreign-root controls.
  The preliminary warming probe was absent. The next packet extends actual
  dispatch to staged new and resume seeds on both backend and copied-setup
  paths, including literal seed insertion, completion and parent/child
  execution correlation. It remains a no-mux synthetic-backend test, not
  actual Copilot or either live installation cycle.
- The expanded packet exposed intermittent five-second finite phase timeouts,
  including no-seed controls, so the earlier 30-case pass is not robust
  qualification. Temporary test-owned diagnostics caught timeouts in setup,
  spawn observation and return; finite-process stack dumps located a full
  `__main__` import through the receipt writer's monitor lock-path lookup.
- Monitor runtime helper lookup now consults only an already-loaded command
  module for compatibility overrides; otherwise it uses the existing local
  implementation. New controls cover both no-command-import paths and
  preserved loaded-CLI overrides. All temporary copied-helper instrumentation
  and bootstrap warming probes have been removed for the clean packet.
  The original five-second native deadline remains unchanged.
- The synthetic PowerShell backend uses generic-host seed append semantics;
  its expected argv now reflects that actual boundary. Copilot-executable
  delimiter insertion remains separately covered by the provider controls.
  This fixture correction does not change production argument handling.
- The clean CLI-free packet passed 31 controls but still failed five actual
  consumer cases. Receipt dispatch also loaded the entire mutation-owner
  roster, as the finite stack diagnostics had shown. Direct fallbacks for
  the six existing seed/receipt verbs now import their declared canonical
  writer only; all other and unknown direct verbs retain full-roster
  discovery, and daemon capability/registration paths remain unchanged.
- The canonical registered handlers, authority fencing, versions, logging and
  ambiguous-write behavior are unchanged. Added controls verify the six
  owner identities, unchanged malformed receipt rejection and full discovery
  for unknown finite-looking names. The tracking-writer suite, finite-provider
  suite and clean actual consumer matrix are under combined validation;
  no production phase deadline was increased.
- The combined writer/provider/runtime packet passed 82 cases and failed
  four real-runtime consumers under the five-second cap. At this observed
  contract crossroads, the operator explicitly selected `review-ten`: revise
  native Windows phase calls to a ten-second cap through normal review and
  validation. The literal decision is retained in the inception transcript.
- Updated the native adapter and launch contract accordingly. Separate pane
  lifecycle calls remain five seconds, mux queries remain two seconds, and
  retries or resident Python waiters are still forbidden. A new real stalled
  child control measures the ten-second cutoff, checks the child is stopped
  before the owner returns and checks a single launch. It joins the adapter,
  writer, provider and real-runtime seeded matrix in the current validation
  packet; approval of a deadline is not acceptance of an unrun test.
- The approved-bound packet passed all 100 tests: the real stalled-child
  watchdog, provider and writer controls, adapter cases and all seven
  completed-runtime consumer cases. New/resume seeds retain literal payloads,
  actual PID/birth and exit observations, one-time completion and correlated
  outer/backend receipts. This is still a synthetic backend without a mux.
- Publication review moved the expensive construction matrix into the existing
  opt-in `tests/native/` lane instead of growing unconditional unit CI. Only
  its discovery location and docstring changed; collection at the explicit
  path is being verified. F/E9 and the 13-plugin install-contract gate pass.
  The parent Plan and Validation Plan remain open.
- All seven cases collect successfully at the explicit native path. Final
  F/E9, the full module-size ceiling check, headless launch census, native
  adapter PowerShell parsing and the 13-plugin install contract pass.
  Documentation, contract and performance impact were rechecked against the
  candidate; publication remains PR-gated, and nothing is deployed.

### 2026-10-27 - Consumer PR review

- Opened #9288 through the managed flow after preserving a recovery branch and
  rebasing onto current `dev`. The only conflict was documentation; both the
  concurrent fleet paragraph and the native test guidance were retained.
  The independent installer/removal work remains with #9253's owner.
- Exact-head Copilot review `5673353889` identified one Medium issue:
  admission-only setup clears the seed fields and bypasses the adapter's
  console-control guard through its direct backend branch. The outer pane
  protects a different process and does not close that gap.
- The candidate now guards that existing host's direct native wait, retaining
  same-host PowerShell-script execution and unchanged legacy behavior. New
  full copied-setup ConPTY cases use the actual completed runtime/provider
  and canonical outer pane Job, checking first-interrupt survival, actual
  nonzero exit and the correlated return receipt. Validation and thread
  resolution are pending; the review is not yet a merge gate pass.
- Two local review-fix packets each passed five controls and failed one before
  terminal-backend readiness/preparation; each backend mode passed separately,
  but that is not a combined passing result. Corrected the test's working
  directory to the recorded checkout and bounded whole-pipeline observation
  separately from the unchanged ten-second native phase cap.
- Added a default-off Windows-only selector to the existing manual acceptance
  workflow for independent exact-dispatch-SHA qualification of the six focused
  controls. The strict workflow catalogue tests pass all 87 cases after
  accounting for already-declared inputs/jobs and the new lane. Required unit
  CI and unrelated default diagnostics are not expanded.
- #9288 is draft while that native proof is pending. Local failed attempts
  remain failures, the Medium thread remains unresolved, and there is no
  installed rollout or live-host effect.

### 2026-10-27 - Consumer review corrections and independent qualification

- Windows acceptance run `40584656272`, job `122472827124`, exercised exact
  head `6c6737f38d540b11d365dcf5dba0a8f71baf1a5c`: all six focused controls
  passed, with 16 deselected and no skips. Both full copied-setup terminal
  modes passed through the completed runtime/provider and canonical pane Job.
  The admission-only interrupt finding's thread is resolved. Earlier local
  startup refusals remain failed measurements, not evidence of a latency fix.
- Fresh Copilot review `5674644985` confirmed that correction but identified
  another Medium issue: a normally returning PowerShell backend can leave
  `$LASTEXITCODE` unset, incorrectly producing an interruption receipt.
  Normalize that code to zero only on the normal-completion tail; preserve
  the unset sentinel for early exits.
- The new same-owner script regression has no explicit `exit` and requires
  actual zero exit plus the correlated `returned` receipt. It and all four
  existing copied-setup controls passed together (five passed, ten deselected).
  PowerShell parsing and staged F/E9 lint passed.
- Preserved the fix on a recovery branch, then rebased the same PR onto
  `3666cd4098`. The only conflict was the installer declaration digest;
  recomputed it from the merged installer, retaining both the concurrent
  installer changes and this PR's native helper deployment entry. Rebased
  validation and fresh review remain pending. No runtime was updated, no live
  session was stopped, and the parent Plan and Validation Plan remain open.

### 2026-10-27 - Normalized Windows consumer merged; launch admission next

- #9288 merged as `0fceb0024d139eccd0da739a212e5197889a11af` after exact-head
  Copilot approval `5675119539`, required green checks and the managed
  Maintainer merge flow. Windows run `40592162495`, job `122498894011`, pinned
  to `5dd6070e1aa709226f7858b767d3e280ca120c5e`, passed seven focused cases,
  16 deselected and no skips. The rebased complete native-shell file passed
  all 15 cases locally. These close the bounded normalized Windows consumer
  slice, not the general parity, live-session or installation-survival gates.
- Re-read the canonical Plan and Validation Plan. Next implement and prove
  same-worktree check/create exclusion only until mux discoverability, with
  recheck/join on contention and parallel different-worktree launches. Retain
  the existing seed and lifecycle authorities and #5579's forwarding ownership.
- The same worktree continues this effort; it is not finalized. Automatic
  squash pull-forward aborted safely; after verifying the merged tree exactly
  matched the published local tree and retaining a recovery branch, advanced
  onto current `dev` without replaying the already merged commits.
- #9253 remains open under its installer/removal owner; the YAML intent
  trackers #9022/#9040 are closed, but installed guidance delivery and the
  surviving-session standby controls remain unverified here. Normal release
  and authorized selected-host rollout have not been verified. No target
  runtime, service, session, state or configuration was changed.

### 2026-10-27 - Shared Windows mux creation candidate

- Implemented one session-keyed kernel lease shared by the Windows Picker
  and detached new-session primitive. Both recheck before creation and on
  contention. The lease releases at native mux discovery even when the
  creation client is still alive, before receipt waits, attach or backend
  lifetime. The seed-admission lease is not reused.
- Late joins do not build a second detached pane or claim/type a seed.
  Picker contenders dispose only their unused handoffs and follow the
  existing join/cold-seed policy. Bridge acquisition retains its live-mux
  refusal; exact-restart receipts do not credit another creator's mux.
  Failed native discovery cannot invoke creator cleanup, and failure cleanup
  does not run after releasing the creation lease to other launchers.
- The isolated engine packet passed 134 cases without skips, including two
  cross-language native overlap controls. The related Manager packet passed
  49 cases with ten POSIX-only skips on Windows. The overlap fixture is a
  small system-compiled executable; earlier Python-proxy startup failures
  were failed measurements, not reasons to extend the production query cap.
  The strengthened actor-specific query handshake passed both native cases,
  without skips, in 12.16 seconds. Owner imports finish behind a ready/go
  preparation barrier before native creation begins. Local validation uses
  the same 60-second per-test fixture budget as the selected CI lane; no
  production phase, query or creation deadline was expanded.
- Fixed seven encountered cold-seed test fixtures to answer the current
  hosting-admission contract before modeling an older seed interface. No
  production hosting policy was weakened. Install-contract validation passed
  for all 13 runtime plugins.
- The default-off, exact-SHA Windows acceptance job includes the two focused
  overlap cases without expanding required unit CI. Draft #9320 published
  head `f1f097ccfaa5aef7fed72746d39eff3a382ae8a0`; Windows run `40605886719`,
  job `122545157686`, passed nine focused cases, 16 deselected and no skips,
  in 81.04 seconds. This is native component acceptance, not rollout.
- Direct final review found that a failed query after dispatch could be
  mistaken for definite non-creation. Retain the pending pane arguments as
  uncertain instead; the focused regression packet passed nine cases.
  Required CI also caught growth beyond `sessions.py`'s shrink-only ceiling.
  Move check/create orchestration into its existing mux-creation owner,
  without widening the baseline. Fresh combined validation passed 135 cases,
  without skips, and the focused native packet passed both cases with literal
  spaces, embedded quotes, trailing backslashes and empty arguments.
  The module-size gate passes without a baseline edit. Updated-head native
  acceptance, review and required CI remain pending for that follow-up.
- The follow-up head `c1ee29675ec65c5b55f30a04568e7b14065ea9c4` passed the
  selected Windows job again (run `40609005414`, job `122555632654`):
  nine passed, 16 deselected and no skips in 79.26 seconds.
  Copilot review `5676376600` then found one High issue: a nonzero client
  return after publication could still delete a live pane's manifest.
  Preserve every dispatched engine client's uncertain handoff; Picker also
  preserves receipt/token files after publication and skips both retry paths.
- The review fix passed eleven engine failure/admission controls and all
  thirteen Manager creation controls. Native overlap passed both directions
  with literal arguments and a stale-exit-code sentinel. The fixture now
  leaves the parent harness home unchanged and overrides only actor
  environments; readiness diagnostics distinguish preparation from creation.
  One preparation-only failure preceded that isolation correction and is not
  production latency evidence. The opt-in Windows job additionally selects
  the single published-failure/retained-handoff control.
- Required CI caught missing launch-census classifications for the new
  bounded client calls. Updated exact caller-contained counts and passed the
  coverage guard, without adding a resident service or weakening a guard.
  These review/census corrections still await publication, fresh review and
  exact-head acceptance; the High thread remains open until the fix is up.
- This is a bounded Windows creation primitive, not complete per-worktree
  launch admission: the tracked engine lifecycle fence still serializes its
  protected section across a repository, `pane_create` retains its separate
  new-session fallback, and POSIX is unchanged. Those
  requirements remain open rather than deleting the existing deletion-race
  protection. Neither live installation, session custody, release/rollout nor
  two-cycle removal acceptance has changed.

### 2026-10-27 - Windows creation primitive merged; full admission continues

- #9320 merged as `f1748bc4afa510b9dbb9cfd2633289e8fcf1251b` through the
  managed Maintainer flow. Fresh Copilot review `5676713459` on exact head
  `9569ab560923e27724d95b1d2e65d9e70ec06bf4` reported no open findings;
  its clean `COMMENTED` verdict on this owner-authored PR satisfies repository policy.
  The earlier High thread was fixed and resolved, and required CI was green.
  A body-only validation update refreshed the required check; the first merge
  remained blocked until that new check passed. No CI bypass was used.
- Exact-head Windows run `40615486375`, job `122576873953`, passed nine
  copied-setup/script/mux controls (16 deselected) in 77.77 seconds and the
  Picker failure control (one passed, 12 deselected) in 0.72 seconds, with no
  skips. These are native component results, not actual PSMux, live rollout or
  installer-removal acceptance.
- Re-read the canonical Plan and Validation Plan. Closed only the bounded
  Windows primitive/component subitems. Kept whole-path admission, separate
  pane creation, POSIX, real mux/terminal behavior, standby, release/installed
  proof and both selected-host cycles open. The already merged #9253 removal
  prerequisite remains distinct from those live gates.
- Pull-forward aborted safely on squash replay. All 20 owned PR paths exactly
  matched the merged tree; the remaining tree delta belonged to the separate
  merged wheel-transport change #9319. Retained recovery refs and advanced
  onto the verified merged default without replaying this PR's commits.
- Next stretch: preserve deletion/reclaim safety while replacing the broad
  tracked-launch lifecycle serialization with per-worktree admission. Inventory
  the real destructive callers before changing their fence. Include the
  `pane_create` fallback without changing explicit new-pane intent into
  accidental seed delivery to an existing pane. Retain the existing seed owner
  and coordinate #5579's separately owned forwarding work.
- The same worktree continues this active effort and is not finalized.
  No live installation, service, banner, native conversation or terminal was
  changed. Source merge is not a containing release or installed proof.

### 2026-10-27 - Provider-first per-worktree retirement fencing

- Traced physical retirement through finalization, routine cleanup/reap-one,
  explicit managed removal and the managed sweep. Each now takes a distinct
  kernel lifecycle lease before its fresh record/liveness checks and retains it
  through physical removal. Repository mutation locking and seed/native
  admission remain separate; forced removal cannot skip this lease.
- Compatibility review rejected immediate removal of the repo-wide launch
  barrier: independently running older retirement owners do not understand
  the new lease. The current slice expands both sides while preserving the
  old barrier. Full launch parallelism remains unimplemented until qualified
  peer support permits migration; no deployment or daemon restart is implied.
- The contained focused regression packet passed 217 tests in 433.46 seconds
  (710.34 seconds including preparation and cleanup). It covered lifecycle
  exclusion/release, forced managed removal, cleanup revalidation, managed
  sweeping and PR finalization rollback. An earlier aggregate-limit run did
  not finish and is not counted as a pass. After the compatibility-barrier
  correction, the final launch/embodiment packet passed 119 tests in 53.78
  seconds (392.59 seconds including preparation and cleanup). Its real child
  controls retain the legacy repository barrier while proving per-target
  exclusion, independent leases and release after exit or termination.
- Documentation impact: updated the plugin's native-admission reference,
  this effort's launch contract and `TESTING.md`; no vision intent changed.
  This is the provider-first expansion toward the existing per-worktree
  exclusion intent, not closure of whole-path or either live cycle.
- The effort remains Active in this same worktree. No selected-host backup,
  installation, service, banner or native conversation has been changed.

### 2026-10-27 - Additive lifecycle fence merged; migration remains gated

- #9339 merged as `5456dacedae6e3f6a94cedef90f708ee18c0e295`.
  Copilot review `5677462473` approved exact head
  `42c427580cc5694dfe87fdc831d9859390e01d26` with no open findings;
  required CI and source attribution passed. The managed Maintainer flow
  used its ordinary additional-review exception, not a CI bypass.
- Managed publication rebased onto current `dev` without changing the ten
  tested source/test paths. The 217-test retirement packet and final
  119-test launch packet remain applicable. Normal managed pull-forward
  then advanced this worktree to the verified merge, retaining recovery refs.
- Re-read every Plan and Validation item. Closed only the provider-first
  lifecycle expansion subitems; split peer qualification into its own open
  gate. Whole-path parallel admission, pane creation, POSIX, real mux/TTY,
  maintenance standby, release/installed proof and both live cycles stay open.
- Next stretch: trace the existing resident retirement owner's readiness and
  version/capability/cutover contract. Determine how launch can qualify the
  actual independently running owner before narrowing repository locking,
  including old/unknown-peer and rollback controls. Do not equate current
  source or payload with current resident support, introduce a parallel
  daemon, or activate a fleet update as a side effect of that investigation.
- This same worktree continues the effort and is not finalized. No host
  installation, service or native conversation was changed.
