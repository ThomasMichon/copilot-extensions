# Machine-Restart Session Recovery

[Parent effort](README.md) - [Tracker #4564](https://github.com/ThomasMichon/copilot-extensions/issues/4564)

## Guiding Intent

Restore eligible, previously live native CLI conversations after their execution
environment restarts, without reviving deliberately closed sessions, duplicating
survivors, or granting the resumed agent new operational authority.

This is a focused extension of the existing Worktree Manager effort, not a
second session registry or a new daemon. It is vision-closing against
[session-hosting](../../../visions/session-hosting/README.md):
`Durable liveness snapshot`, `recoverable-across-full-restart`,
`snapshot-outlives-the-process`, `persist-before-notify`, and
`launch-receipts-are-provisional`. Existing agency and hosting boundaries remain
binding. Any newly necessary standing intent must be reconciled before runtime
implementation, not silently inferred from this plan.

## Request

Operator request, verbatim:

> We should make a little effort to see if it's possible to add automatic agent restoration post-restart of the machine. Basically, worktree-manager can add a handler for a restart notification, as well as have a "black-box" to track live sessions periodically, and then on successful re-launch of the daemon, it can carefully restore the previously-live sessions. Of course, it will need to detect when sessions exit gracefully. When resuming, it can pass a resume seed that clearly indicates a restart occurred, and the agent should check status before proceededing.

Execution direction, verbatim:

> Great. Write out or extend an effort for this, and then kick a CLI headed agent via agent-bridge to work on it

The operator authorized planning and delegated implementation. Production
reboots, daemon restarts, host boot/logon configuration changes and live
automatic-recovery enrollment are not implied by that direction.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Planning coordinator | Land this reviewed proposal and verify the CLI worker's pickup | Existing public tracker and planning PR |
| Restart recovery implementer | Own the scoped implementation, validation, review and completion record | Native CLI/mux session through agent-bridge; source handle `steady-resistor` |

## Coordination

- **Topology:** one isolated implementation worktree; normal PRs to `dev`.
  The planning coordinator stops editing that worktree before worker pickup.
- **PR ownership:** the coordinator owns the plan PR through merge; the
  implementer owns subsequent PRs through their real review and merge gates.
- **Scope:** Manager-owned recovery mechanics and the smallest necessary
  owner-protocol extensions. Preserve process boundaries: agent-worktrees owns
  worktree/head/claims and native session acquisition; agent-bridge owns message
  delivery; agent-dispatch owns task policy.
- **Existing owners:** #4564 retains reachability/history and manual restoration.
  Preserve #1478 and #6677: deliberately ended manual agents remain dormant.
  Do not take unrelated control-plane, fleet, release or workflow work.
- **Milestones:** report the reconciled contract, the first failing/passing
  recovery tests, first integrated implementation, and final reviewed result
  through the same bridge conversation. Record material outcomes here.
- **Continuity:** use native context continuation where supported. Do not
  replace the implementing conversation merely to refresh context.

## Context

The Manager already has a singleton mux daemon, a disk-backed mapping registry,
incarnation/revision fencing, bounded attachment observation and a native
seeded cold-Resume path. #5573 delivered attached-client observation; it did not
deliver machine-restart restoration.

The mapping is not a sufficient recovery ledger today: it does not bind every
entry to its original native conversation and boot identity, and a failed mux
probe may tombstone an entry without recording why execution disappeared.
Pane-exit observations and session-end hooks are useful evidence, but neither a
generic tombstone nor an exit code alone distinguishes intentional closure
from shutdown teardown.

## Plan

The following implementation shape and safeguards are **agent-recommended**
means of fulfilling the operator's request. They require normal source review.

### 1. Reconcile the contract

- [ ] Trace current launch, head/session acquisition, teardown and daemon-start
  paths against current source. Record the minimum owner-protocol additions.
- [ ] Define recovery eligibility, intentional closure, shutdown interruption
  and unknown evidence separately. Include detached but genuinely live agents;
  attachment count is presentation evidence, not eligibility.
- [x] Define supported native environment boundaries, including Windows versus
  WSL and native Linux. Same-boot daemon updates must not masquerade as reboot.
  The contracts delivered through #7252 and
  [#7263](https://github.com/ThomasMichon/copilot-extensions/pull/7263)
  distinguish Linux/WSL evidence epochs, native Linux's unqualified notification
  transport, and explicitly unsupported Windows/macOS/container recovery.
  Same-boot evidence cannot become a frozen restart candidate. This defines the
  boundary; it does not qualify notification timing or automatic restoration.
- [ ] Specify capability-gated, explicit local enrollment and report-only
  inspection. Unsupported or unprovable environments report a blocker rather
  than silently becoming a different launch mode.
  Default-off configuration and report-only capability inspection landed in
  [#7252](https://github.com/ThomasMichon/copilot-extensions/pull/7252);
  supported startup enrollment and recovery admission remain unresolved.

### 2. Preserve the black box and exit evidence

- [ ] Persist last-positive hosting evidence at lifecycle transitions and on a
  bounded periodic cadence, under the existing attributable installation.
  Bind the exact native conversation, project/worktree, host environment,
  boot epoch, execution incarnation and observation sequence/freshness.
- [x] Preserve recovery evidence independently of current reachability and
  display tombstones. Store only necessary metadata in private local state,
  not transcripts, secrets, environment dumps or untrusted executable commands.
  The evidence foundation landed in [#7230](https://github.com/ThomasMichon/copilot-extensions/pull/7230);
  lifecycle producers and restoration remain separate open items.
- [x] Record reasoned intentional-exit evidence durably; delayed observations
  from an older incarnation must not retire a replacement.
  Source-bound native lifecycle producers and owner corroboration landed in
  [#7241](https://github.com/ThomasMichon/copilot-extensions/pull/7241).
  Linux/WSL source qualification does not qualify platform shutdown adapters or
  authorize recovery; native restart/seed integration remains open below.
- [ ] Handle native restart/shutdown notification by checkpointing the eligible
  generation before teardown. Reconcile cancelled shutdowns. Do not rely on
  this notification being delivered during a crash or sudden power loss.
  The gated Linux transport and cancellation fixtures landed in #7252. Resident
  blocking-work latency remains unqualified; Windows/WSL notifications remain
  explicitly unsupported, so this item is not complete.
- [x] Surface failed, corrupt, stale or unavailable evidence explicitly.
  Ambiguous closure stays unconfirmed; absence of a clean-exit hook is not proof
  that automatic resurrection is authorized.
  The evidence, notification and frozen-identity increments through #7263
  preserve explicit blockers, reject malformed storage without reset, retain
  stale/uncertain evidence without authorization, and leave ordinary mux
  startup usable. Current recovery admission and launch failures remain
  separate obligations below.

### 3. Reconcile and restore on startup

- [ ] Admit recovery before ordinary dead-mux reconciliation can erase the
  previous boot's useful evidence. Freeze candidate generations and serialize
  recovery through existing ownership mechanisms.
  Pre-admission candidate freezing landed in #7263 under the active lease and
  drain-visible mutation fence; current owner admission is still unresolved.
- [ ] Revalidate current worktree existence, native transcript/session identity,
  current head/succession, task policy and holds through their actual owners.
  Terminal, superseded, intentionally closed or uncertain candidates must not
  launch automatically.
  [#7271](https://github.com/ThomasMichon/copilot-extensions/pull/7271)
  delivered a provisional exact current-worktree check and explicit single
  frozen-attempt inspection.
  [#7280](https://github.com/ThomasMichon/copilot-extensions/pull/7280)
  delivered opt-in current task-owner snapshots for one explicit local/central
  task ref, binding native owner/generation, holds and the unread steering
  inbox. Persisted claim uncertainty survives owner load/save without becoming
  active-claim proof. These are provisional reads, not action-time admission
  or generation reservations; mixed/producer/inbound claims and profile policy
  remain unqualified. A matching snapshot cannot authorize launch.
- [ ] Reuse a proven surviving session first. Otherwise request supported
  native cold Resume of the exact original conversation in its proper mux.
  Never substitute a new conversation, raw process spawn or a global repair
  command that mutates unrelated sessions.
  The explicit restart-only owner increment now fences the original UUID,
  old source process/epoch and current owner record through durable reservation
  and mux invocation. It admits no task/claim/profile-bound worktree and is
  not wired to automatic startup. Actual native readiness/identity/seed
  acknowledgement and surviving-session adoption remain unresolved.
- [ ] Persist recovery attempt and outcome; reconcile a crash between launch
  and receipt, concurrent startup, seed timeout and delayed registration before
  retrying. Do not claim exactly-once delivery merely from a pre-launch flag.
  #7263 retains frozen candidate identity and pre-commit crash rollback, not
  launch reservation/outcome or acknowledged seed pickup.
  The subsequent explicit owner increment persists `launch-uncertain` before
  invocation and distinguishes `mux-created` without permitting retries.
  Manager read-only inspection composes this receipt with the immutable frozen
  attempt. Outcome reconciliation through actual native acknowledgement
  remains open; a frozen Manager row no longer implies no invocation.
- [ ] Apply bounded admission and concurrency. Authentication, dependency or
  ownership failures remain visible and resumable without busy retry loops.

### 4. Seed, startup integration and source delivery

- [ ] Deliver an explicit restart seed using the existing supported native
  prompt transport. Identify restart recovery, require current status and
  objective/claim/hold reconciliation, and prohibit interpreting the seed as
  renewed permission for physical or service operations.
  The explicit owner increment builds the full interactive STATUS FIRST seed
  into exact-original Resume argv. This proves the construction/transport
  boundary, not actual native seed consumption or acknowledgement.
- [ ] Verify expected native identity, ready CLI/prompt, fresh registration
  where that optional capability is available, and acknowledged seed pickup.
  Launch, registration and completed restoration are distinct outcomes.
- [ ] Integrate with the existing daemon's platform lifecycle rather than a
  parallel scheduler. Provide the supported boot/logon startup path needed for
  automatic operation, with explicit enrollment and least necessary authority.
  Ship platform implementations or visibly capability-gated refusals; do not
  call untested platform behavior complete.
- [ ] Update actual architecture, configuration, inspection and operator
  recovery documentation. Add required changefiles for touched release units.
- [ ] Drive coherent implementation PRs through review and merge; report
  merged, released and installed states separately. Retain explicit rollout
  authorization and deployment ownership instead of silently enabling recovery.

## Validation Plan

All items below are **agent-recommended** validation obligations.

- [ ] Establish failures against the pre-change behavior before fixing the
  ledger/reconciliation paths; execute the targeted bounded repository runner.
- [ ] Cover intentional exit before/after a periodic checkpoint, delayed old
  exit hooks, shutdown-time process termination, cancelled shutdown, abrupt
  power loss and absent final notification.
- [ ] Cover stale snapshots, torn/corrupt state, denied writes, unavailable boot
  identity and unreachable dependencies without empty/success-shaped fallbacks.
- [ ] Cover same-boot daemon restart and zero-downtime replacement: survivors
  remain untouched and no automatic duplicate launch occurs.
- [ ] Cover concurrent startup, crash after claim and after successful launch,
  late registration, seed timeout despite a ready prompt, and retry after
  uncertainty. Prove no replacement conversation or duplicate native process.
- [ ] Cover changed head, pruned/missing worktree, concluded task, explicit
  manual pause/exit, changed profile, missing transcript and authentication wait.
- [ ] Exercise actual CLI/mux plus restart-seed acknowledgement in disposable
  fixtures or the clean-room rig. Simulate boot epochs; do not reboot or kill
  production sessions to obtain evidence.
- [ ] Exercise supported platform notification/startup adapters and preserve
  Windows/WSL distinction; disclose any remaining lane-specific qualification.
- [ ] Verify ownership, private-state handling, bounded resource use, existing
  manual Resume/restore and optional standalone behavior remain intact.
- [ ] Complete documentation-impact review, required test/installation gates,
  provider review and actual merges. Resolve or transfer every remaining item
  to a named tracked objective before recording this slice complete.

## Completion Gate

The slice is not complete at a ledger prototype, registered process or opened
PR. The eligible original-conversation recovery path, graceful-exit exclusion,
restart seed and platform startup contract must be implemented and qualified,
or individual residual items explicitly transferred to named tracked work.
Any live rollout still requires separate authorization and running-state proof.
The umbrella effort remains Active independently of this slice's completion.

## Journal

### 2026-10-18 - Planning and delegation

- Reused #4564 rather than opening a duplicate tracker. Its completed #5573
  observer remains the starting point; manual task dormancy under #6677 is
  preserved. Captured both operator requests above and separated the proposed
  implementation safeguards from requested behavior.
- Assigned the dedicated native CLI implementer after this proposal's review
  gate. Implementation and platform qualification remain open; no host
  configuration, session recovery or automatic startup has been enabled.

### 2026-10-19 - Evidence foundation and existing-daemon integration

- The reviewed planning proposal merged in #7224. The declared restart recovery
  implementer picked up the focused slice; other umbrella ownership is unchanged.
- Added a private recovery-evidence journal, not another worktree/session
  authority. Positive evidence carries canonical native identity and native
  boot/environment/incarnation metadata; reasoned exit events preserve the last
  positive observation and reject stale-generation teardown. Shutdown-pending
  and cancelled-notification state are distinct from explicit closure and
  unconfirmed loss.
- Added the minimal read-only engine corroboration mode
  `session-role --hosting-evidence`. It reuses current head/session and native
  process/mux-binding owners, rechecks owner state after probing, and never
  performs succession, task policy, claims or launch decisions itself.
- Integrated inspection before the existing active daemon's status server and
  bounded periodic sampling inside its existing lease/drain fence. The first
  regressions failed before the evidence APIs existed; the integrated bounded,
  network-disconnected regression run passed 204 Manager tests and 64 Worktrees
  tests, with three native-platform skips. A drain-fence regression found during
  integration was fixed before that run. Subsequent final-diff qualification is
  recorded with the source contribution.
- A subsequent source self-check added failing regressions for a native process
  restarting inside an unchanged mux. Evidence now also carries native process
  birth identity and the canonical owner lifecycle revision, fencing delayed
  old-native exit and observation events without inventing a second succession
  authority.
- Provider review added explicit dotted-id, same-mux succession, mapping-only
  refresh, repeated unknown-exit and live mux-incarnation corroboration cases.
  Mapping freshness is separate from execution identity, so a rejoin cannot
  reopen an intentional close or discard shutdown state. The canonical native
  binding helper supplies the current mux incarnation without introducing a
  second process-discovery implementation.
- A further review identified SQLite hot rollback journals after an interrupted
  write. A real disposable child now crashes during a spilled transaction; the
  regression verifies report-only inspection preserves the files, then the
  lease-holding daemon recovers the last committed evidence before sampling
  resumes. Missing or incompatible databases are never recreated by this read.
- The next review identified PID reuse before a probe: stable birth checks alone
  could pair a stale conversation lock with another Copilot process. The owner
  now corroborates the native UUID through its actual open per-session hold-file
  inode, with birth and descriptor/path revalidation. Real held-file fixtures
  cover the wrong-session process and a valid positive control; unavailable
  proof stays unconfirmed. This adds no secondary session registry and leaves
  conservative general liveness semantics unchanged.
- Added report-only inspection and explicit unqualified-platform reporting.
  `automatic_eligible` remains false. The first source slice does not complete
  this plan: actual native notification/startup adapters, lifecycle producers,
  enrollment/admission, exact-conversation cold Resume, acknowledged status-first
  seed delivery and remaining native qualification are still open. No live
  recovery, host configuration, daemon restart or rollout was performed.

### 2026-10-19 - Evidence foundation merged; lifecycle slice next

- [#7230](https://github.com/ThomasMichon/copilot-extensions/pull/7230) merged to
  `dev` at `62b095042dbc1c143635bb813fe7986e6e35081b` after independent Copilot
  approval of exact head `c76c40e81868acb314051084d1d3fde297cbfab6`, with no open
  findings, and the actual required PR gate passing in
  [CI run 39222700357](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39222700357).
  The initial canonical merge refusal while that gate was missing was respected.
- The final bounded, network-disconnected regression receipt is tied to source
  tree `71cda78cdd58f66c40dcb56cf477ff5cd60cd080`: 229 Manager tests passed with
  three native-platform skips, and 226 Worktrees tests passed. The selected lane
  includes the journal/observer, native ownership proof, existing mux daemon,
  attachment, binding and session contracts. Private raw logs are retained with
  the implementation record, not published.
- Canonical post-merge completion initially stopped on already-landed incremental
  replays. The documented owning-branch conflict continuation preserved current
  upstream content and its documentation additions; only inspected empty replays
  were skipped. The original full recovery reference remains retained. The
  resulting branch and every tracked path matched fresh `origin/dev`, then
  canonical `pr-complete` succeeded.
- Only independent evidence persistence is closed by this foundation. #4564 and
  the umbrella effort remain open. Next is the bounded reasoned-exit/lifecycle
  producer contract: bind observations to their original native generation,
  preserve shutdown interruption versus explicit closure, and keep unavailable
  or ambiguous cause unconfirmed. Native notification/startup, enrollment,
  owner-authorized exact-conversation Resume, acknowledged status-first seed
  delivery and remaining platform qualification still follow.
- This records a source merge, not a verified release, installation or running
  recovery. No live enrollment, host configuration, shared-daemon restart,
  production-session operation or rollout was authorized or performed.

### 2026-10-19 - Source-bound lifecycle producer

- Extended the existing owner activation intervals with optional opaque source,
  reasoned End and persistent ambiguity metadata. Qualified Linux/WSL Start
  evidence requires the actual original UUID's open native hold, captured with
  original ancestor process birth and boot/environment metadata. It does not
  create a second registry, invent a source during periodic observation, or
  alter launch environment/authority.
- Both lifecycle writes require the version-3 owner verb, even for legacy
  requests. Source rejection precedes self-session claim acquisition. A new
  physical source opens a new interval; a true same-source bind is idempotent.
  Delayed old Start delivery cannot reactivate an ended claim. End matches a
  unique canonical historical source under the registration lock; historical
  facts cannot close a replacement or release its claim/fsmonitor. Ambiguity
  from repeated logical intervals in one physical CLI survives history pruning.
- The warm End path now processes the original hook context instead of
  acknowledging an unhandled event. It never probes/attaches a terminal or
  recaptures resident ancestry. Its compatibility capability and source-bound
  fallback flag prevent silent old-runtime downgrades. Start likewise requires
  source-aware acknowledgement and never falls back to older startup writers;
  original binding environment is transported rather than inherited from the
  resident. Windows native source
  capture remains visibly unqualified without a new warm-path helper spawn.
- One read-only owner query supplies current hosting and an explicitly requested
  prior native conversation's bounded lifecycle. The Manager samples retained
  journal subjects even after display tombstones. Qualified source and ordinal
  corroboration are required for reasoned native closure. Shutdown precedence is
  checked inside the journal transaction; delayed End after cancellation stays
  uncertain rather than guessing cause from wall-clock order.
- The first historical-End/current-claim regression failed on the old behavior.
  The final integrated bounded, disconnected lane passed 456 Worktrees tests
  with 12 platform-specific skips and 232 Manager tests with three native skips.
  This selected lane also covers real old-daemon refusal, unchanged hook/daemon
  boundaries and resident-to-canonical historical End.
  This is source/fixture evidence, not native restart or seed-acknowledgement
  qualification. Publication and actual merge are recorded separately.
- `automatic_eligible` remains false. Native shutdown notification, startup and
  enrollment, recovery admission/attempt reconciliation, exact native Resume,
  acknowledged status-first seed and platform qualification remain open. The
  full plan and #4564 are not completed by this producer slice.
- The actual PR module-size guard rejected growth in three existing modules.
  Extracted the generic activation model, locked title capture and captured
  lifecycle subprocess transport along their responsibility boundaries.
  Compatibility aliases and the source-string transport guard follow those
  owners. The tracking baseline only shrank; no ceiling was widened. The final
  targeted run above also covers the original title-capture contracts.
- Provider review found three concrete compatibility gaps. Source-aware warm
  Start and End now require the selected rendezvous to advertise their capability
  before a packet is sent, retaining the response check as well. Real legacy
  TCP peers prove that refusal leaves their mutation handler uncalled. All three
  installer paths deploy lifecycle transport and the canonical stdlib
  source-capture helper beside the client; disposable installed-bin fixtures
  cover source loading without the payload's package tree.
- The Manager now accepts the owner's full 256-interval retained lifecycle,
  without confusing that count with its monotonic activation ordinal. The
  boundary regression accepts 256, rejects 257, and observes a latest-source
  End at ordinal 1000. These regressions failed before the fixes. The subsequent
  bounded isolated lane passed 478 Worktrees tests with 12 platform skips and
  234 Manager tests with three native skips. This remains pre-merge fixture
  evidence, not permission to enroll or claim live restoration.
- A fresh review identified two additional Medium findings in its overview,
  despite an empty inline-open count. Unknown/inferred End observations now
  preserve the execution's cancelled-shutdown marker before a delayed
  `user_exit`; an explicit operator stop still closes it. Retained intentionally
  closed subjects without live mappings no longer consume periodic owner probes,
  while a fresh live mapping re-admits its subject through the existing revision
  fence. All five new regression cases failed first. The targeted isolated
  Manager lane then passed 239 tests with three native skips; the unchanged
  Worktrees slice retains the preceding 478-test receipt.
- The next review found PID-only catalog reconciliation replacing a qualified
  activation with a source-less interval. The shared activation helper now
  refuses source-less lifecycle changes once source-bound history exists, while
  the catalog can still fill PID metadata. Both matching and changed catalog
  timestamps reproduced the failure. The regression follows canonical Start,
  actual catalog refresh, matching End, claim release and fsmonitor cleanup,
  then proves another catalog pass cannot reopen the ended interval. The
  expanded isolated Worktrees lane passed 505 tests with 12 platform skips;
  Manager sources remain unchanged from their 239-test receipt.
- A further review identified replay side effects: duplicate historical End
  could capture a replacement's title, and a timestamp-less source-bound Start
  could reacquire an ended interval's released claim. Title capture now runs
  only for an actual current End or the legacy closure shape. Hook reactivation
  of a previously ended physical source requires a usable event timestamp and
  is refused before claim acquisition when uncertain. Timestamped logical
  resumes retain their explicit ambiguity. The title regression now patches its
  actual leaf owner and proves redelivery is side-effect free; all three
  missing/invalid-timestamp cases failed before the fix. The expanded isolated
  Worktrees lane passed 509 tests with 12 platform skips; Manager remains at
  its unchanged 239-test receipt.
- Review then exposed a distinct title-persistence interleaving after current
  End had already released its lock. The source-bound End result now carries
  the exact activation source and ordinal. The title writer rechecks that
  receipt against the latest ended interval under the record lock, alongside
  current-head authority. Missing receipts cannot authorize source-bound title
  writes. Both same-UUID replacement windows failed before the fix: replacement
  before title input and replacement after the old input had been read. The
  unchanged-generation positive control still captures its title. The expanded
  isolated Worktrees lane passed 512 tests with 12 platform skips; Manager
  remains unchanged from its 239-test receipt.
- Review found two remaining compatibility branches: source-aware End had
  bypassed legacy cleanup before reaching the authoritative writer, and a new
  activation reusing an already-linked handoff token had not advanced lifecycle
  revision. End now always delegates to the version-3 writer, which decides
  legacy versus protected history under its lock without inventing source
  proof. The reused-token branch advances revision only when activation changes
  without a new handoff transition. Three failing-first cases cover both
  resident and cold legacy cleanup plus replacement revision; negative controls
  retain bound-source protection. A stale save cannot erase the replacement,
  and idempotent rebinding does not advance revision. The isolated Worktrees
  lane passed 517 tests with 12 platform skips; Manager remains at 239.
- Review identified that warm End had not transported its original scoped
  bare-resume binding. End now carries only the three project/worktree/session
  binding variables, alongside native source metadata, without a terminal probe
  or environment dump. A real resident-IPC regression uses two records with the
  same native UUID, outside-worktree cwd and an explicit binding to the later
  record; it first failed by selecting the earlier record and leaving the
  intended claim open. The repaired path closes only the bound activation and
  leaves the unrelated record byte-for-byte unchanged. The isolated Worktrees
  lane passed 518 tests with 12 platform skips; Manager remains at 239.
- Review exposed lost source authority after every interval for a still-live
  physical source had been pruned. Each session now retains a separate bounded
  discarded-source fence; superseded source re-registration is refused before
  claim mutation, and discarded sources cannot regain End or owner-proof
  authority. The fence survives stale and equal-revision saves. Overflow becomes
  a sticky explicit refusal, not forgotten history. Legacy activation parsing
  and serialization moved to the existing lifecycle leaf, reducing the tracking
  module ceiling from 4108 to 4030 lines without widening a budget.
- The same review found unknown-cause definitive hook Ends still diluting owner
  polling. A strict backward-compatible journal marker records consumption
  separately from cause. Consumed unmapped subjects remain retained but leave
  the rotation; live mappings re-admit them. Inferred Ends remain probeable, and
  shutdown/cancellation uncertainty is preserved. Marker persistence is fenced
  to the execution generation. The two original regressions failed before these
  fixes. The expanded isolated lane passed 763 Worktrees tests with 12 platform
  skips and 249 Manager tests with three native skips. This is still source and
  fixture validation, not released or deployed recovery.

### 2026-10-19 - Lifecycle slice landed; notification and admission next

- [#7241](https://github.com/ThomasMichon/copilot-extensions/pull/7241)
  squash-merged to `dev` as `834dfd278d` after the exact source head received
  a clean Copilot review with no open findings and all required checks passed.
  The final isolated contracts passed 763 Worktrees tests and 249 Manager tests,
  with platform-specific skips. The source-bound intentional-exit Plan item is
  resolved; the remaining Plan and Validation Plan items are still open.
- The later CI execution was affected by a cancelled timeout after its test
  output already reported success. Its original logs and the official Actions
  incident reference were preserved. After the incident was resolved, one
  explicitly authorized provider-native retry of that exact cancelled job on
  the unchanged source head succeeded, followed by the required aggregate.
  No workflow, timeout, repository rule or release setting was changed.
- Next stretch: explicit local recovery configuration and capability reporting,
  followed by a native shutdown-notification adapter owned by the existing
  active mux-daemon lifecycle. Preserve Linux versus WSL/native Windows
  boundaries, cancelled-shutdown handling, bounded drain ownership and
  unsupported-platform refusals. Validate against disposable notification
  fixtures, never a production reboot, live enrollment or daemon restart.
- Exact original-conversation recovery admission, task/hold reconciliation,
  durable attempts and acknowledged status-first seed delivery remain separate
  unresolved implementation and native-qualification obligations. This merge is
  not release, rollout or completion of the recovery objective.

### 2026-10-19 - Default-off configuration and native notification transport

- Added strict local `restart_recovery` preferences for shutdown notifications
  and automatic restoration, both default off. Read-only evidence inspection
  exposes requested capabilities without opening a system-bus connection or
  creating absent state. Unsupported platforms and requested-but-unqualified
  automatic restoration visibly block; configuration is never admission.
- Added the native Linux logind transport to the existing active mux-daemon
  loop and mutation admission. It negotiates a real delay inhibitor before
  subscription, validates the unique owner and message shape, reads initial
  preparation state, checkpoints before releasing the FD, and persists matching
  cancellation before reacquiring. Denial, owner/bus loss, malformed evidence
  and storage failures do not elevate or busy-retry. Drain and lease release
  close owned native resources; passive generations never connect.
- A shutdown checkpoint refuses SQLite lock contention immediately and applies
  a progress deadline with rollback. The transport retains received-FD ownership
  even when a coroutine is cancelled before consuming the method reply, because
  the upstream library does not close such dropped reply descriptors.
- Configuration/capability regressions first failed in 18 cases, then passed.
  The integrated bounded, disconnected lane passed 318 tests with three native
  skips, including the actual private D-Bus/SCM_RIGHTS fixture. That fixture
  ran in a disposable derivative of the pinned isolation image with Debian's
  D-Bus daemon present; the standard image lacks the binary and explicitly skips
  that proof. No host system bus, shutdown, live enrollment or rollout was used.
- This is transport qualification, not a qualified automatic-recovery platform.
  Existing blocking work on the resident loop can exceed logind's notification
  delay. The adapter explicitly reports `qualified: false`; notification timing
  remains an open obligation under this focused objective before it can
  authorize automatic recovery. Windows/WSL shutdown adapters, startup enrollment,
  owner/task/hold admission, durable attempts and acknowledged exact-UUID
  status-first Resume remain open. No Plan or Validation Plan item is closed
  solely by this increment.
- The first native callback integration also exposed End arriving before the
  cancellation signal. The original journal only cancelled still-live pending
  rows, retaining an ended generation's obsolete shutdown authority. Both
  native-cause regressions failed first. Matching cancellation now makes that
  ended generation unknown, clears its notification and preserves the sticky
  cancelled-shutdown marker; an explicit operator closure stays closed.
- Provider review then identified lost notification identity after same-boot
  adapter replacement. Five new regressions failed for initially cancelled and
  still-preparing native states, ended/live generations, and multiple retained
  IDs. Startup now recovers only this environment/boot's IDs, reconciles initial
  cancellation before reporting `watching`, and retains ongoing IDs for later
  atomic batch cancellation. Foreign epochs, replacement executions and
  explicit operator closures retain their fences. A denied cancellation write
  leaves the adapter blocked rather than reporting readiness.
- A further provider pass identified three failure paths: a server-thread health
  read racing adapter teardown, non-UTF-8 configuration escaping recovery
  admission, and a timed-out inhibitor request leaving its connection open.
  Six new regressions failed first. Health retains a local adapter snapshot;
  shared configuration normalizes decoding failures; permanent transport
  failure disconnects immediately and drains its tasks/descriptors outside the
  running event-loop callback. The expanded isolated lane passed 332 tests with
  three native skips, including an actual separate-loop private-bus late-FD
  reply after the real inhibitor timeout. Automatic restoration and resident-loop
  notification timing remain unqualified.
- The subsequent review distinguished a native setup-cadence defect from that
  disclosed blocking-work timing limit. A real private-bus daemon fixture first
  failed at the production one-second sweep cadence. The existing idle wait now
  services native I/O on the same loop in admission-fenced, at-most-50-ms
  slices; default-off, absent and blocked adapters keep ordinary sleep without
  busy pumping. No worker or scheduler was added. The expanded disconnected
  lane passed 337 tests with three native skips, including actual daemon setup,
  same-thread drain admission and no-spin contracts. Blocking owner/probe/status
  work still prevents a qualified shutdown-latency claim.

### 2026-10-19 - Notification increment landed; full restoration remains open

- [#7252](https://github.com/ThomasMichon/copilot-extensions/pull/7252)
  squash-merged to `dev` as `81b4186db8`. The exact published head
  `20b25acc36` received a genuine Copilot approval with no open findings, then
  passed required CI before managed merge. The final disconnected related lane
  passed 337 tests with three platform skips.
- This closes delivery of default-off preferences, read-only capability
  reporting and the gated native Linux transport increment, not either full
  enrollment or notification qualification. No full Plan or Validation Plan
  item is newly checked solely by this merge. The umbrella remains Active.
  Merged source is not a promoted release, installed runtime or enabled recovery.
- The next bounded stretch is owner-mediated candidate admission and durable
  attempt identity before any launch is permitted. The existing exact-head
  bridge acquisition is unseeded; the normal seeded embodiment releases its
  lifecycle fence before seed delivery. Recovery therefore needs an explicit
  exact-head fenced request, current task/hold policy from its owner and a
  separate native pickup acknowledgement. Neither immutable dispatch provenance
  nor a ready prompt can substitute for those receipts.
- Retain the separate notification timing obligation: the daemon's ordinary
  attachment/owner probes, status publication and registry locks can block
  despite the repaired inter-sweep I/O service. Automatic restoration stays
  blocked until that capability and the owner/seed/startup gates are qualified.

### 2026-10-19 - Frozen candidate identity before launch admission

- The historical positive capture now retains the owner's existing worktree
  creation nonce and asserted head revision through an additive capability.
  Legacy nonce metadata is not allocated or guessed; its capture remains
  explicitly unqualified for recovery. Both owner-emission and Manager-capture
  regressions failed first. Adding metadata cannot reopen a cancelled execution.
- With an explicit automatic-restore request and the existing active daemon
  lease, startup durably freezes qualified previous-boot candidates before
  ordinary reconciliation. The bounded private attempt store deduplicates the
  exact source/target identity and preserves its original snapshot through a
  same-boot daemon replacement or later observation. Default-off/passive paths
  do not write it; inspection never creates absent state or resets corruption.
- The integration first failed because startup had no durable attempt result;
  the separate store contract initially reported its missing implementation.
  The expanded disconnected lane passed 316 Manager tests with three native
  skips and 28 owner tests, including real-process concurrent deduplication and
  a child exiting after insert but before commit. Capacity rollback preserves
  prior unresolved identities rather than evicting them.
- This is a pre-admission identity, not a launch attempt outcome: `frozen` is
  awaiting current owner/task/hold admission, and `automatic_eligible` remains
  false. Native launch/crash reconciliation, acknowledgement, enrollment and
  notification timing remain open. No full Plan/Validation item is closed by
  this unmerged increment.
- Provider review found non-text SQLite columns escaping the structured blocker
  and truncated storage being mistaken for a new database. Six regressions
  first failed, including the initial/promoted activation mutation boundary.
  Stored column types are now validated; first initialization atomically
  publishes a complete schema without replacing an existing file. Truncation
  and orphaned transaction sidecars remain blockers, preserving pending identity.
  Initial activation and passive promotion use the existing drain-visible
  mutation boundary under the active lease. The expanded disconnected Manager
  lane passed 327 tests with three native skips; the unchanged owner slice
  retains its 28-test receipt. These remain pre-merge source/fixture results.
- The next review identified an out-of-range integer timestamp escaping the
  shared evidence constructor. The 400-digit value reproduced an unwrapped
  overflow in both the attempt store and its source journal. The shared numeric
  check now bounds finite values without a potentially overflowing float
  conversion; excessive JSON nesting is normalized at the shared decoder.
  Inspection preserves malformed state and reports a blocker while actual mux
  service still starts. The expanded disconnected lane passed 331 Manager tests
  with three native skips; owner sources remain unchanged from their 28-test
  receipt.

### 2026-10-19 - Frozen identity increment landed; current admission next

- [#7263](https://github.com/ThomasMichon/copilot-extensions/pull/7263)
  squash-merged to `dev` as `fe5b070278` after a genuine no-findings Copilot
  approval on exact published head `7ed0b64319` and required green CI.
  The final disconnected Manager lane passed 331 tests with three native
  skips; unchanged owner sources retained their 28-test receipt.
- Resolved the Plan's environment-boundary definition and explicit
  evidence-failure reporting items. The source/target freeze is delivered, but
  neither current-owner admission nor recovery execution/acknowledgement is
  complete. The focused restoration gate and umbrella effort remain open.
- Next stretch: current owner-mediated head/incarnation/transcript/liveness and
  task/claim/hold admission, followed by a fenced exact-conversation native
  Resume request and explicit status-first seed pickup acknowledgement.
  Extend the frozen identity with durable attempt/outcome reconciliation;
  do not treat a candidate, staged prompt, pane readiness or registration as
  permission or completed recovery.
- Retain separate startup enrollment, blocking-work notification timing and
  disposable native/platform qualification gates. This merge does not imply
  release promotion, installed runtime, automatic enrollment or rollout.

### 2026-10-19 - Provisional exact current-worktree checks

- Added a bounded, explicit owner check for a frozen native conversation,
  worktree creation nonce, asserted head/lifecycle revision, source activation,
  path and current target epoch. It rereads the owner after process observation;
  changed/paused/terminal/pending, indirect or absent paths, ended/ambiguous
  sources, absent transcripts and live/uncertain native processes refuse.
- Task provenance, claims and bound/profile-assigned worktrees remain
  conservatively unqualified, not interpreted as current permission. A reduced
  worktree match is only a provisional snapshot: `launch_authorized` and
  `automatic_eligible` are false. Ended shutdown activations remain refused.
  This does not complete the Plan's current task/claim/hold admission item.
- The Manager can explicitly check one already-frozen attempt through the
  attributed owner process with no-update/no-reconcile guards. Response
  capability/version, exact typed expectation and target epoch are checked.
  Even a match returns a blocker/nonzero status for current task policy,
  fenced launch and acknowledged restart Resume. Ordinary inspection remains
  owner-call-free; the resident loop gains no work or blocking latency.
- Both new test surfaces first failed against the absent implementation.
  The targeted disconnected lane passed 24 Manager and 75 owner tests,
  including an actual child reading persisted owner YAML and a separate real
  engine-process boundary with unchanged frozen SQLite bytes. Native epoch and
  process observations are synthetic; no conversation, host startup or service
  state was changed. Full restoration, current task policy, durable launch
  outcomes, seed acknowledgement, startup enrollment and notification timing
  remain open. This increment is not yet reviewed or merged.
- The expanded disconnected compatibility lane passed 196 Manager tests with
  three native-platform skips and 172 owner tests, including exact project
  binding and existing session
  lifecycle, hosting, frozen-storage and ordinary mux contracts. This is source
  validation, not a claim of native restart or shutdown qualification.
- Provider review of #7271 found that the boolean mux-presence helper collapsed
  failed probes into absence. Five regressions first failed. The check now uses
  the existing tri-state listing: spawn denial, timeout and failed listing stay
  unknown; confirmed empty/unrelated listings are distinct from an existing mux.
  Both owner fixtures use that contract, including canonical dotted-name
  matching. The disconnected owner lane passed 179 tests; Manager sources are
  unchanged from their 196-test/three-skip receipt. The review fix still awaits
  a new exact-head verdict and required CI.

### 2026-10-19 - Provisional worktree check landed; current task policy next

- [#7271](https://github.com/ThomasMichon/copilot-extensions/pull/7271)
  squash-merged to `dev` as `936926d76a` after genuine Copilot approval on
  exact head `177fbef149`, a resolved mux-uncertainty finding and required green
  CI. The final disconnected owner lane passed 179 tests; unchanged Manager
  sources retain the 196-test/three-platform-skip receipt.
- The Plan's combined current worktree/task/claim/hold admission item stays
  open. Only the provisional exact worktree checks are delivered; they remain
  read-only and return no launch authority. The full restoration/validation
  gate and umbrella effort are still open.
- Next stretch: compose current task ownership/generation and holds through
  the actual policy owner, preserving optional standalone behavior and
  rejecting terminal, released, reassigned, suspended/held or uncertain work.
  Reuse owner process boundaries rather than interpreting immutable task
  provenance or a generic claim-status response as launch permission.
- After current policy admission, finish action-time exact-head native Resume,
  durable launch/outcome/crash reconciliation, explicit status-first seed
  acknowledgement, startup enrollment and notification timing/qualification.
  This merge is not release promotion, installation or live enrollment.

### 2026-10-19 - Read-only current task-owner policy increment

- Added an opt-in task-owner API mode that reads reduced task policy and
  active/preparing waiter presence under one SQLite read snapshot. The existing
  claim-status callback requests its versioned response only with explicit
  writer selection; an old coordinator that ignores the query remains
  unavailable rather than supplying assumed-safe missing fields.
- The callback suppresses coordinator autostart, SSH probes/tunnels,
  control-credential fetching and configured shared-token commands. Ordinary
  status output and transport defaults are unchanged. There is no task
  mutation, reservation, token minting or launch.
- Composed the response through the existing verified claim-provider boundary.
  One active producer-free explicit local/central task claim can match only the
  exact project lane, machine/worktree and original native UUID, with a positive
  current generation and no hold, steer, requested resume or waiter. Legacy refs,
  mismatched/opaque provenance, other/mixed/released/producer claims and inbound
  ownership remain unqualified; current generation is not historical continuity.
- Snapshot and owner integration tests first failed. The disconnected lane
  passed 205 dispatch, 195 worktree-owner and 16 Manager contracts, including
  actual child HTTP/callback processes and unchanged persisted owner state.
  High-signal Python lint, module-size and documentation guards passed.
  These receipts are protocol fixtures, not native recovery qualification.
  This increment is not yet reviewed or merged.
- The expanded dispatch compatibility lane passed 361 tests, including
  coordinator routes, existing client authority and task-worker commands.
  The worktree-owner and Manager receipts above are unchanged.
- The combined Plan item stays open: action-time task/claim/profile admission,
  exact-head fenced native Resume, durable launch/outcome reconciliation,
  status-first seed acknowledgement, startup enrollment and notification timing
  remain owed. Automatic recovery remains blocked; no rollout is implied.
- Provider review of #7280 found a real steering-inbox gap: answering a form
  clears `awaiting_steer` even while submitted instructions remain unread.
  The new regression failed first. The policy now reads unconsumed inbox
  presence in the same SQL snapshot, requires explicit Boolean `pending_steer`
  evidence at the worktree boundary, and refuses until the last instruction
  is consumed; no steering content leaves the owner. The final disconnected
  lane passed 365 dispatch and 198 owner tests; unchanged Manager source
  retains its 16-test receipt. The fix still awaits a new exact-head verdict
  and required CI; the full restoration gate remains open.
- The next review of #7280 found that persisted unknown claim state was
  normalized to active before the policy check. Eleven real owner-load
  regressions failed first, also covering malformed collections/items silently
  disappearing into an apparently empty ledger. The codec now preserves an
  explicit opaque metadata marker through full/public projections and later
  saves, retaining conservative active finality handling and opaque
  placeholders rather than granting active-claim proof. Known/legacy valid
  shapes are unchanged. The expanded disconnected owner/claim/finality lane
  passed 1,163 tests; unchanged dispatch/Manager sources retain their 365/16
  receipts. This fix still needs exact-head provider review and required CI.

### 2026-10-19 - Current task-owner policy landed; fenced Resume next

- [#7280](https://github.com/ThomasMichon/copilot-extensions/pull/7280)
  squash-merged to `dev` as `5635c2df1b` after genuine exact-head Copilot
  approval on `53920971f2` and required green CI. Both review findings were
  addressed with failing-first regressions: unread steering cannot pass via
  cleared form flags, and normalized/malformed persisted claims cannot become
  active or absent-ledger proof. The final disconnected receipts are 365
  dispatch and 1,163 owner/claim/finality contracts, with unchanged Manager
  sources retaining 16 owner-check contracts.
- A stale source-attribution check lost a PR-description race; its later
  normal metadata-event check passed. The actual required check set was green
  before merge; no workflow retry or review bypass was used.
- Reconciled the combined current worktree/task/claim/hold Plan item as partial,
  not complete. Current explicit task snapshots are delivered, but there is
  still no action-time task/claim reservation, fenced restart launch,
  acknowledged seed pickup or native recovery qualification.
- Next stretch: reuse the existing exact-conversation native Resume boundary,
  with expected-head/incarnation preconditions and durable attempt/outcome/
  crash reconciliation; then prove explicit status-first seed acknowledgement
  without treating pane readiness or registration as acknowledgement.
  Startup enrollment and blocking-work notification timing remain open.
  This merge is not release promotion, installation or automatic enrollment.

### 2026-10-19 - Exact-original restart launch and durable owner uncertainty

- Added an explicit restart intent to the existing `embody` boundary. The
  request binds the frozen native UUID and owner/head/source revisions to
  old PID/birth/epoch evidence; same-boot, foreign, changed, ended, live,
  unknown or pending-seed targets refuse. All task/resource/profile and
  dispatch-bound targets remain unqualified rather than treating provisional
  policy snapshots as action-time reservations.
- Reused lifecycle and per-worktree native admission, with strict record-writer
  exclusion covering final revalidation, durable uncertainty and mux invocation.
  The private bounded per-worktree owner receipt is file/directory-synced before
  launch. Repeated requests read the existing result; uncertainty, timeout or
  owner crash cannot authorize another invocation. A different request cannot
  overwrite an unreconciled receipt.
- Native argv carries the exact original UUID and full interactive STATUS FIRST
  restart seed, with no fresh conversation or post-fence prompt typing.
  `mux-created` is not ready-native proof, seed acknowledgement or restoration;
  all corresponding completion/automatic flags remain false. The Manager's
  explicit `--check-launch` only reads and binds the owner outcome to its frozen
  identity; it does not alter attempt state or enable an automatic caller.
- The first 16 launch contracts failed against the old CLI. The disconnected
  lane subsequently passed 252 owner/embody and 42 Manager contracts, including
  real competing CLI processes and `os._exit` after the native invocation
  boundary. Those process tests uncovered stale-lock output contaminating JSON;
  the shared fence now routes that diagnostic to stderr. Mux/native epochs and
  process state remain simulated; no Copilot process or live host was changed.
- This source increment still needs provider review and required CI. The full
  restoration gate remains open: actual native identity/readiness and STATUS
  FIRST acknowledgement, task/claim/profile action-time admission, startup
  enrollment, surviving-session adoption and notification blocking-work timing
  remain obligations. No release, rollout or enrollment is implied.
- The final expanded disconnected lane passed 284 owner/admission/embody and
  76 Manager attempt/outcome contracts. High-signal lint, unchanged module
  ceilings and documentation consistency passed. Native process readiness and
  acknowledged seed pickup are still not qualified by these synthetic fixtures.
