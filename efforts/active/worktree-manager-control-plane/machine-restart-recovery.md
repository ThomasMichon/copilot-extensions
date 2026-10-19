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
- [ ] Define supported native environment boundaries, including Windows versus
  WSL and native Linux. Same-boot daemon updates must not masquerade as reboot.
- [ ] Specify capability-gated, explicit local enrollment and report-only
  inspection. Unsupported or unprovable environments report a blocker rather
  than silently becoming a different launch mode.

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
- [ ] Record reasoned intentional-exit evidence durably; delayed observations
  from an older incarnation must not retire a replacement.
- [ ] Handle native restart/shutdown notification by checkpointing the eligible
  generation before teardown. Reconcile cancelled shutdowns. Do not rely on
  this notification being delivered during a crash or sudden power loss.
- [ ] Surface failed, corrupt, stale or unavailable evidence explicitly.
  Ambiguous closure stays unconfirmed; absence of a clean-exit hook is not proof
  that automatic resurrection is authorized.

### 3. Reconcile and restore on startup

- [ ] Admit recovery before ordinary dead-mux reconciliation can erase the
  previous boot's useful evidence. Freeze candidate generations and serialize
  recovery through existing ownership mechanisms.
- [ ] Revalidate current worktree existence, native transcript/session identity,
  current head/succession, task policy and holds through their actual owners.
  Terminal, superseded, intentionally closed or uncertain candidates must not
  launch automatically.
- [ ] Reuse a proven surviving session first. Otherwise request supported
  native cold Resume of the exact original conversation in its proper mux.
  Never substitute a new conversation, raw process spawn or a global repair
  command that mutates unrelated sessions.
- [ ] Persist recovery attempt and outcome; reconcile a crash between launch
  and receipt, concurrent startup, seed timeout and delayed registration before
  retrying. Do not claim exactly-once delivery merely from a pre-launch flag.
- [ ] Apply bounded admission and concurrency. Authentication, dependency or
  ownership failures remain visible and resumable without busy retry loops.

### 4. Seed, startup integration and source delivery

- [ ] Deliver an explicit restart seed using the existing supported native
  prompt transport. Identify restart recovery, require current status and
  objective/claim/hold reconciliation, and prohibit interpreting the seed as
  renewed permission for physical or service operations.
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
