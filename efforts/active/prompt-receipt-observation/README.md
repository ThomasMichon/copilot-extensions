# Prompt Receipt Observation

- **Slug:** `prompt-receipt-observation`
- **Repo:** copilot-extensions
- **Branch(es):** managed proposal and implementation PRs
- **Created:** 2026-10-22
- **Status:** Blocked
- **Vision:** `visions/plugins/context-handoff/README.md`: continuous work,
  durable recovery locator, objective and authority boundary
- **Umbrella issue:** #7942

## Guiding Intent

First-prompt receipt observation must never delay a user's turn on runtime
provisioning, identity discovery or an unavailable optional worktrees service.
Observation remains distinct from consuming a baton or acquiring ownership.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| hinged-socket | Proposal, implementation, validation and source PR owner | Managed source worktree; #7942 |

## Coordination

- **Topology:** sequential proposal and implementation PRs.
- **Host (owns PRs):** hinged-socket.
- **Delegates:** none; required independent review uses repository review.
- **Handoff:** source qualification packet; release and installed adoption
  remain outside this bounded source objective.
- Preserve existing source claims. Collision detection is read-only forge/task
  inspection; no contact with pre-existing Bridge agents, agent-chat or remote
  worker dispatch. Any review requiring such contact is a blocker, not an
  exception. Only campaign-owned native workers or newly created owned local
  agents may participate when explicitly needed.

## Context

The first `user.message` callback currently calls `agentWorktreesGet` and
`logHandoffPromptReceived` synchronously. Their shared CLI path may resolve
and provision an optional runtime. Existing async non-provisioning helpers
provide a seam, but merely wrapping synchronous work in a promise is insufficient.
This closes existing continuity intent without changing hosting authority.
Design principles: optional siblings degrade independently; payload-only
shape remains; Windows and POSIX share the lifecycle contract.

## Request

Public-safe excerpts of the authorized bounded request:

> "make first-prompt recovery receipt observation asynchronous and non-provisioning"
>
> "Implement cheap locator capture, launch-provided identity preference and bounded
> async receipt observation. NO synchronous execFileSync/installer from prompt event,
> no per-event unbounded queue/process/thread."
>
> "Missing runtime/blocked identity/absent worktrees service must not stall user's
> turn; preserve idempotent replay, pending/unavailable state and later
> reconciliation. Ownership/consumption authority is unchanged."
>
> "Whole hook objective normally instant, <=1-2s worst-case; configured timeouts
> not measured proof."

Scope: context-handoff extension/core/timeouts, their tests and directly related
documentation. Excludes native launch scripts, worktrees cache/readiness,
Manager, telemetry, mention guard, installed payload and shared services.

## Plan

### Phase 1 — Reviewed proposal
- [x] Land this bounded plan through repository review and required CI.

### Phase 2 — Observation implementation
- [x] Capture only the first prompt's canonical locator without synchronous CLI
  calls; prefer existing launch identity without inventing authority.
- [x] Use one bounded async observation, with coalesced duplicates, explicit
  pending/unavailable/logged outcomes and later reconciliation.
- [ ] Prevent provisioning and bound the complete child lifecycle, including
  cancellation/cleanup, rather than only promise scheduling.
- [ ] Preserve receipt format, replay behavior and all consumption/ownership checks.
- [x] Implement the approved atomic backend, bounded history output and late-session
  recovery corrections; retain process-custody qualification as a separate blocker.
- [ ] Update consumer documentation, add changefile, review and merge source.

## Validation Plan

- [ ] Unit coverage: launch identity, missing runtime, blocked identity, absent
  service, concurrent duplicates/replay and deferred availability.
- [ ] Native consumer integration: exercise the actual registered callback with
  blocked child processes and verify return latency, event-loop responsiveness,
  process cleanup and no installer descendants.
- [x] Isolated first-touch execution with no provisioned runtime.
- [x] Measure callback and lifecycle durations; target normally instant callback,
  at most 1-2 seconds worst case. Do not call configured deadlines evidence.
- [ ] Run applicable source checks and required CI; assess independent review.
- [x] Record genuinely available safe external qualification, or explain skipped
  tiers without implying installed/released/live proof.

## Proposal

Approved in #8014. Implementation may refine internal seams after the plan clears;
new authority or ownership behavior requires a separate proposal.

## Journal

### 2026-10-22 — Bounded source intake

- Confirmed open #7942 has no existing implementation PR or competing task.
- Created isolated managed source worktree `hinged-socket` and claimed the issue.
- Traced the synchronous prompt callback, identity helper and receipt logger.
- Captured the authorized scope and latest coordination restrictions. No existing
  agent session was contacted and no shared runtime was changed.

### 2026-10-22 — Proposal reviewed and merged

- #8014 received Copilot approval and green required CI; merged as
  `b2ed7bf3b24e47e82197f36db2ccd7a2292902fb`.
- Re-read the plan after merge. Implementation and all runtime validation remain
  open. The managed sync refused the deleted merged PR ref; a clean, backed-up
  local rebase onto fetched `origin/dev` dropped the already-applied proposal.

### 2026-10-22 — Source implementation and local qualification

- Registered callback now captures the locator and schedules one coalesced
  observer. It has no synchronous CLI or runtime resolution. Qualified launch
  identity avoids a lookup; other sessions retain binding-first async discovery.
- One-second shared subprocess budget plus bounded tree termination replaced an
  initial 1.5-second design: the initial tree-cleanup measurement was 2.13 seconds,
  exceeding the requested ceiling. The revised runs stayed below two seconds.
- `node --test` over `prompt-receipt.test.mjs`, `cli-timeouts.test.mjs` and
  `handoff-core.test.mjs`: 117 passing tests. Largest observed final callback:
  0.10 ms; first-touch lifecycle 1,689.82 ms; blocked identity and tree cleanup
  1,827.39 ms; cancellation and cleanup 1,711.35 ms; deferred successful native
  receipt 838.43 ms. These are measurements, not universal latency guarantees.
- A second native Windows run from `pythonw.exe`, monitoring only its own
  descendant windows across timeout and cancellation cycles: ten passing receipt
  tests, 93 samples / 24 owned processes, zero visible owned windows and zero
  owned foreground transitions. Callback maximum 0.10 ms; first touch 1,692.08 ms,
  timeout cleanup 1,677.05 ms, cancellation cleanup 1,225.78 ms, deferred success
  745.76 ms. Fixtures and captured output were removed.
- First-touch uses the real source runtime resolver under an isolated empty home,
  not an installed runtime or provisioner. Deadline expiry remains honestly
  unavailable. Native success uses a controlled CLI child, not a live service.
- `python tools/check-install-contract.py`: all 13 runtime plugins conform.
  The Python plugin runner rejects `.mjs` selectors by contract; these tests use
  the existing Node test runner directly. Python 3.9 cannot run repository tooling;
  the already-installed Python 3.11 ran the contract and changefile tools.
- External/installed SDK-host qualification and Linux execution were not run:
  this bounded source lane has no authorized external deployment, remote worker
  dispatch is prohibited, and no shared runtime was changed. The tested production
  registration helper is wired by an extension-source contract assertion.
  Release promotion and installed adoption remain separately owned, not claimed.
- Documentation impact: the plugin README now explains observation scheduling,
  failure/retry and cleanup. No configuration, consumption or ownership change.

### 2026-10-22 — Independent review blocks source completion

- The implementation review identified five substantive unresolved cases:
  cross-process check/append races; failed tree termination retaining inherited
  pipes; output-limit termination bypassing tree cleanup; unbounded retained
  activity history; and a captured missing session ID not being refreshed.
- Prior passing measurements remain evidence for their exact controlled cases,
  not complete lifecycle/idempotency qualification. Reopened the corresponding
  plan/validation checks. No implementation merge or installed rollout occurred.
- **Decision needed:** the scoped implementation needs an atomic receipt
  operation at the existing data owner and a reliable process-custody primitive
  that survives root exit/termination failure. The current context-handoff-only
  CLI composition cannot make the backend check/append atomic, and best-effort
  `taskkill` is not a custody guarantee. Confirm the owner and permission for
  those additional source seams, or select a reviewed replacement design within
  context-handoff. Do not introduce a parallel activity store or silently widen
  this lane into shared backend/lifecycle work.
- Preserve this worktree and draft implementation PR pending that decision;
  source review and merge remain required. Continue without contacting existing
  agents, agent-chat, remote workers or shared installed services.

### 2026-10-22 — Approved bounded backend continuation

- The coordinating owner authorized tightly coupled receipt persistence,
  bounded history and missing-session recovery changes. Reuse existing canonical
  locks, transactions and durable stores; no new generic ledger or authority
  framework. Process custody must first reuse qualified platform/agent-procutil
  APIs; no new broker or native Job primitive and no competing native-launch edits.
- Refreshed issue, PR, task and source-file evidence before implementation.
  Existing ownership remains intact. `activity.py` and `handoff_trace.py` have
  no intervening upstream delta; the only matching receipt PR is this candidate.
  Existing process-custody PR #7833 remains untouched.
- Backend design: one atomic receipt operation under `handoff_trace._append_lock`
  in its existing per-project/worktree trace. Stream matching with bounded
  record buffers returns one compact result instead of exporting history.
  The trace's existing cleanup owns retention. No new ledger is introduced.
- Missing captured session IDs may be filled only from the current session
  object's known identity on later reconciliation; known identities and the
  original locator remain unchanged.
- Implemented the approved backend slice in `handoff_prompt_receipt.py`,
  integrated the existing activity commands and bounded result consumer.
  Forty-seven contained backend/trace tests pass, including two real contending
  processes, history larger than 64 KiB, oversized records, partial-tail recovery,
  legacy receipt migration and fsync failure. Touched Python passes Ruff F/E9.
- The process primitive investigation is now concrete:
  [exact symbols, ownership, reproductions and minimal integration contract](custody-boundary.md).
  Existing qualified `run_caller_owned` handles bounded stream custody, but the
  pre-runtime Python-free invocation measured 3,015 / 2,297 ms cold. The present
  adapter leaks Python descendants on failed tree-kill and output overflow;
  Node-only child fixtures concealed this. No generic native primitive was added.
- Full receipt-suite execution is **not green**: first-touch failed its
  <2,000 ms lifecycle assertion (test-case duration 2,663.75 ms) and deferred
  native success exhausted the current budget.
  Do not merge or reinterpret these as pre-existing failures. Retain the draft
  candidate until the owning lifecycle/readiness seam can be integrated and the
  full negative-path suite passes.
- Final backend revalidation: **48 tests passed** through the contained
  `run-plugin-tests.py agent-worktrees` selector for the receipt and trace files;
  Ruff F/E9 and the 13-plugin install-contract gate pass. Seven targeted consumer
  logic tests pass. These do not replace the full native qualification.
- The committed exhaustive Python-descendant regression runs both custody cases
  and fails both: apparent settlement with a live descendant after 2,800 ms.
  Each test then cleans its exact owned processes and fixtures before asserting.
- Completed an all-open-PR file check, including remaining file pages for large
  PRs #5694, #6928 and #7895: no other open PR touches the receipt backend's
  `activity.py`, `handoff_trace.py` or new receipt module. The known process-custody
  and launch-maintenance owners remain untouched.

### Bounded ready-invocation discovery

- Completed the requested read-only continuation, documented in
  [the custody packet's ready-descriptor section](custody-boundary.md#bounded-ready-descriptor-discovery).
  The existing catalog publishes a shim, the binding publishes ownership/root,
  and readiness/completion records do not provide an active interpreter invocation.
- No supported pre-resolved invocation was found in the inspected owning
  startup/config/catalog contracts. Consumption would require a new owner handoff;
  no speculative consumer adapter or native primitive was implemented.
- The smallest proposed handoff includes source/generation-bound exact argv and
  isolated environment plus helper-launch custody. Supplying a Python path alone
  must not shift the leak from the command to the adapter that invokes it.
- Backend commit `851afb2e4` and its 48-backend/7-targeted-consumer test packet are
  unchanged. No additional test suite or stress benchmark was run.
- Prior negative controls terminated their recorded test-child identities in
  cleanup and removed their fixtures; this continuation launched no controls or
  background helpers. Source worktree is retained for the same owning task.

### Resident contract implementation admission

- Read the complete merged #8193 contract. The coordinating owner admitted its
  zero-helper resident direction with H2 retaining backend/Node responsibility.
- The delivery/control owner accepted the exact bounds and backend ABI in the
  [narrow lock-admission packet](lock-admission.md). That packet names the only
  requested canonical helper extension, unchanged defaults and existing callers.
- No helper or new resident implementation has been written while this narrow
  admission is pending. The separate missing creation-nonce delivery field is
  recorded as an owner gate, not inferred.
- Mechanical index-only rebase completed; all other owners' current rows remain
  unchanged. The retained implementation and original negative evidence remain.

### Controlled continuation checkpoint

- The exact optional `handoff_trace._append_lock(..., deadline=None)` extension
  was subsequently admitted and implemented. Default native blocking calls
  remain unchanged; finite deadlines use nonblocking acquisition on the caller
  thread. Sixty-one targeted trace/deadline/resident-backend tests pass.
- Implemented the nonce-qualified in-process resident backend with the agreed
  100 ms lock, total 1 MiB/4,096 rows/200 ms scan and 1,000 ms pre-effect bounds.
  The owner admission guard precedes the trace lock and is revalidated before
  append. Post-append failure/deadline remains unknown.
- Existing blocking consumers: 168 tests passed across claim history/mirror,
  ADO remediation and session binding. No consumer was migrated implicitly.
- Added the async Node resident client and pending-identity scheduler changes.
  A focused 131-test Node run passed (core, timeouts, scheduler and resident
  socket protocol). Its guarded consumer path spawned zero helpers. The stalled
  observation measured 1,917.73 ms; the 2,000 ms ceiling was not increased.
  These socket protocol fixtures are not the final actual-ControlServer proof.
- Removed the rejected child-helper transport from production observation.
  Its original strict failing assertions and helper implementation remain under
  `evidence/legacy-prompt-receipt-custody.repro.mjs` and
  `evidence/legacy-receipt-child.mjs`; they remain historical counterexamples,
  not a qualified transport or a plugin fallback.
- Began the actual ControlServer + real backend + guarded Node integration fixture
  against immutable provider candidate `ded1f7bd5e7121200d7e52a4926632a7cbe8f5a4`
  from #8224. The first seven cases failed during fixture import, before server
  startup: the staged server imported the adapter by its canonical package name.
  The loader now registers that adapter name first and restores it through
  the test monkeypatch. **That last fixture correction has not been rerun.**
- `RESIDENT_RECEIPT_CONTRACT` remains absent: no ready capability is advertised
  from this unqualified candidate. Actual server integration, windowless cycles,
  final review/CI and source merge remain open. Real project/creation-nonce
  producer delivery remains separately owned; missing nonce stays unavailable.
- Work is paused for a controlled owner continuation. No new publication,
  deployment, restart or broad test batch was started after that checkpoint.
  The staged immutable provider/client fixture is retained for the exact next
  targeted integration run; no fixture server or helper remains running.

### Actual-server continuation and replay durability correction

- Refreshed the retained provider fixture from immutable #8224 checkpoint
  `75a4dc1068b5872bd2bc69eab2c5ccc6120527b4`. The first actual-server run passed
  six cases and exposed a fixture-only missing `SessionHandoff.state`; the
  corrected exact pending-successor selector passed. The production head-only
  restriction was not weakened.
- Expanded real ControlServer/backend/Node coverage for trace-lock contention,
  total history byte/row/elapsed limits, oversized records, cancellation,
  storage failure, exact cutover reconciliation, removed associations,
  independent locators and unavailable identity/route/token/capability inputs.
  One expanded run passed 16 cases and exposed a test-only record/query count
  expectation; the corrected selector and ten new identity controls passed.
  These finite results do not establish complete native acceptance.
- Confirmed the provider adapter and ControlServer files are byte-identical
  between that checkpoint and merged #8224 commit
  `eab0a4909575caf9cbd3f09ba7e56831d55c5b92`; refreshed only the test-owned
  provider copy from the merged commit. No mutable owner worktree was copied.
- Independent source review found a real durability defect: a row left readable
  after failed fsync could be acknowledged on replay without another successful
  sync. Exact resident query/record replays now synchronize the matching existing
  history under the same record/trace locks, retain uncertainty on failed/late
  sync, and never append a replacement. Updated the backend and actual-server
  regressions and both consumer/backend docs.
- Fresh post-fix validation: 61 trace/deadline/backend tests pass; the two
  actual-server storage-failure and cancellation controls pass. The latter now
  cancels on a fixture-owned fsync-admission notification, not an arbitrary
  500 ms assumption about server startup. No production timer or workload was
  relaxed. Both the Node and resident paths still report zero helper starts.
- A monitored windowless run passed its two receipt cycles and delayed-fsync
  drain control, with zero observed owned windows/foreground transitions.
  Its fourth case cancelled before admission at 500 ms and therefore did not
  exercise the intended post-admission path. The harness also sampled two
  descendants during final process exit; a subsequent exact-PID check found
  neither running. Preserve the failed report rather than calling this a fully
  green windowless qualification.
- The host was observed at 100% CPU. Do not turn another blind broad rerun into
  a latency claim. The remaining quiet gate is the unchanged full native
  27-case selector followed by the windowless two-cycle/timeout/cancellation
  selector, with recorded non-saturated host conditions and the original
  strict 2,000 ms assertions. No performance acceptance is claimed here.
- Raw local results remain in the untracked
  `.resident-integration-validation/` directory: `run-75a4dc106-first.log`,
  `run-pending-fix.log`, `run-expanded-native.log`,
  `run-identity-controls.log`, `windowless-native.log`,
  `windowless-report.json`, `provider-merged-byte-comparison.log`,
  `run-durability-backend.log`, `run-trace-deadline-durability.log` and
  `run-durability-and-cancel-fix.log`. They are retained test material, not
  publication artifacts.
- `RESIDENT_RECEIPT_CONTRACT` remains absent. The separately owned real
  project/creation-nonce registration producer is admitted but not implemented
  or qualified by this slice. Full producer/control/Node qualification, final
  source review/CI/merge, installed/native-host proof and rollout remain open.
- Normal managed `push-changes --dry-run --json` refused publication because
  the two retained untracked fixture/evidence directories make its worktree
  check dirty. Neither directory was staged, deleted, moved or hidden from
  that guard. The corrective source commit remains local; the existing draft
  still points at `fb6d1592e103de14826e700207c9606b18b07960`. Resolve retained
  test-material disposition through the owning integration lane before retrying
  normal publication; no force, history rewrite or guard override was used.

### Merged subject-producer source composition

- The separately owned subject producer is now merged in #8331 as
  `827860f080ccf9bda456fb0324de785bcc3de85e`. Its reviewed writer adds only
  actual-record `project` and `creationNonce`; registration retains its
  nonblocking record guard, uncached identity/revision/association validation
  and best-effort atomic stamp. No producer source was edited in this lane.
- Fetched only that immutable commit and added its `handoff_diagnostics.py`,
  `session_binding_cli.py` and required `worktree_trust.py` import to the
  retained test-owned provider copy. The adapter/ControlServer files remain
  pinned to merged #8224; their relevant bytes also match #8331's tree.
- Added a real `cmd_register_session` → actual writer → ControlServer →
  backend → Node composition case, with authoritative head-ledger verification
  that the registered subject remains non-head. A second case uses the actual
  writer for an exact pending candidate and a legacy rewrite; the latter
  removes both trusted fields and the Node observer stays unavailable.
- Initial registration composition failed on a missing import in the staged
  older-branch fixture, then on its missing isolated project-authority setup.
  Retained both failures. Added only the immutable missing import and reused
  the repository's existing contained project-authority fixture; no live
  authority guard was weakened. Registration lifecycle services, assignment
  maintenance and checkout trust remain explicit fixture-owned seams.
- Tightened process interception: only the harness's exact Node driver
  invocation is allowed, including on the main thread. The actual producer
  cases cannot silently start a main-thread helper. Both focused composition
  cases pass with zero helpers; Node observations measured 136.83 ms,
  1,024.02 ms and 43.93 ms in that finite run. These are source-composition
  observations, not full native performance acceptance on the saturated host.
- Raw results: `.resident-integration-validation/`
  `run-merged-subject-producer-composition.log`,
  `run-merged-registration-import-fix.log`,
  `run-merged-registration-authority-fixture.log` and
  `run-merged-producer-strict-helper-guard.log`.
- Real producer source availability is no longer the blocker. The unchanged
  full native selector now has 29 cases; quiet-host full/windowless validation,
  complete startup/catalog/SDK-host composition, installed proof and source
  publication/review/CI/merge remain open. The retained fixture/evidence
  publication refusal is unchanged. `RESIDENT_RECEIPT_CONTRACT` stays absent;
  no source or installed readiness/rollout is claimed.

### Full admitted source-gate execution and retained timing failure

- Reconciled the retained consumer head without resetting or rewriting it,
  reread the complete merged producer diff and source completion result, and
  resumed the same task normally. The immutable merged provider/producer
  fixture and unpublished consumer were preserved.
- Ran the requested complete actual ControlServer/backend/Node selector first.
  The old import/setup failures are gone: **28 cases passed, one failed**.
  The host sample was 100% CPU. The storage-failure case exhausted observation
  time before receipt admission, returning unavailable in 1,905.14 ms with
  zero backend calls/effects; it therefore never exercised its intended failed
  fsync assertion. This is a retained pre-admission timing/qualification
  failure, not a passing storage test or a measured >2-second overrun. No
  deadline, workload or assertion was relaxed and no blind full retry followed.
- Added explicit actual-server controls for four retained in-flight requests,
  refusal of a fifth with zero queue, exact uncertain-identity query fencing,
  8,192/8,193-byte request frames and oversized post-commit responses. Added
  one-second/earlier-caller pre-effect clock controls. All six new selectors
  passed: max in-flight four, zero helpers, no fifth backend entry, no repeated
  uncertain mutation, and full settlement before fixture teardown.
- A combined trace/deadline/backend run passed 62 of 63 cases; the new
  pre-effect clock unit test inadvertently entered the real 100 ms lock using
  a different clock, allowing host scheduling to fail it before the intended
  fake-clock assertion. Isolated that unit's lock seam while preserving its
  exact 100 ms admission and 1,000 ms/earlier-caller assertions. Both corrected
  unit selectors pass. Real lock behavior remains covered separately.
- Fresh focused Node core/timeouts/scheduler/socket run: **131 passed**,
  including disposal uncertainty, source/incarnation fencing, frame limits
  and zero-helper observation. Stalled observation measured 1,906.31 ms.
  These tests do not erase the actual-server selector failure above.
- Final owned windowless run: **four passed** (two receipt cycles, delayed
  fsync/drain and post-admission cancellation). The Pythonw parent observer
  collected 638 samples across 25 owned processes, with zero visible owned
  windows, zero owned foreground transitions and no surviving owned processes.
  It measured 1,923.25 ms for the stalled observation. The monitor now checks
  actual process exit state rather than treating a retained dead process
  object as live; previous failed reports remain untouched.
- Extended the real merged registration/candidate cases through the actual
  registered `user.message` callback and resident socket consumer, rather
  than inferring that composition from scheduler unit tests. Both pass.
  Pending-candidate callback: 1.236 ms, full observation 139.01 ms.
  Registered non-head callback: 0.6922 ms, full observation 266.95 ms.
  Each callback returned before any socket I/O; each attempt used exactly
  two sockets and zero helpers. The selected-installation/catalog inputs and
  SDK event shell remain isolated fixtures, not installed-host evidence.
- Raw results are retained in `.resident-integration-validation/`:
  `host-load-before-full-native-1700.log`,
  `run-full-native-merged-provider-producer-1700.log`,
  `run-admitted-capacity-frame-pre-effect-gates-1700.log`,
  `run-trace-deadline-resident-backend-gates-1700.log`,
  `run-pre-effect-clock-only-unit-fix-1700.log`,
  `run-node-core-scheduler-socket-gates-1700.log`,
  `windowless-1700-final.log`, `windowless-1700-final-report.json` and
  `run-real-registered-callback-consumer-gates-1700.log`.
- Current native selector has 33 cases. Its complete qualification remains
  red until the retained pre-admission timing case clears in a suitable
  environment with unchanged bounds. Source readiness is not advertised:
  `RESIDENT_RECEIPT_CONTRACT` remains absent. Complete startup/catalog/SDK-host,
  Linux/macOS/native and installed qualification are not waived. Source
  publication/review/CI/merge remain open; retained untracked material also
  still blocks ordinary publication. No rollout or campaign completion.
- Final touched Python lint, unchanged PR-scoped module-size guard and
  headless-launch source guard pass. The current source draft was rechecked:
  #8026 remains open/draft at `fb6d1592e103de14826e700207c9606b18b07960`.
  Source publication was not advanced past the failed qualification gate.

### Merged shared-guard repair and concrete source-review follow-up

- Verified the existing owner's #8392 repair merged as
  `1bdf8b2d3540379d95f35c66feb50d537cd32c2e`. Took a local safety ref and used
  the normal managed sync once; the branch advanced to
  `6759329e709067466e9d96ee0e834cc637e63b3f` without conflicts. The repair is
  an ancestor and its 69 relevant fixture/registry files match the merged bytes.
  Seven owned consumer/backend/trace/native-test Git blobs were unchanged.
  Checkout CRLF normalization initially confused a raw worktree/blob assertion;
  canonical blob verification cleared it without editing source or evidence.
- Published the refreshed owned draft through the normal provenance-aware
  flow. Fresh CI run `40053222689` passed guards/lint, eligible units with
  coverage and the required PR gate. No competing HTTP-47 patch, guard bypass,
  CI rerun or existing-agent contact occurred. A prolonged local closure check
  was stopped incomplete; no local pass was claimed in place of required CI.
- Review `5636144877` reported three concrete receipt defects on the previous
  candidate; the relevant source was unchanged after refresh, so they remained
  actual blockers rather than being dismissed as stale/pre-existing:
  compatibility query durability, query-window capacity after append, and
  retries across install/activation-generation changes.
- Compatibility matching now synchronizes the same open file before returning
  receipt, including `activity --receipt-locator`; sync failures propagate
  instead of acknowledging a readable row left by failed resident fsync.
- Resident recording now reserves a queryable row and bytes, including any
  separator newline, before append. Boundary refusal leaves history unchanged;
  an admitted last-fitting receipt remains queryable and replayable.
- Pending identity comparison distinguishes durable installation scope from
  current install/activation generations. Stable source/installation/runtime-root
  and subject fences remain; current generations must still match the live
  health/expected-owner handshake. An uncertain retry only queries its original
  subject, while a pre-send unavailable attempt may retry after revalidation.
- Ten new contained Python regressions pass, including the actual compatibility
  activity reader and exact row/byte/separator boundaries. Five selected Node
  functional regressions pass for generation retry and existing identity
  fences. No native/timing suite, archived fixture or historical raw evidence
  was rerun or modified. The original native hold and absent readiness marker
  remain unchanged; the source follow-up needs its own normal CI/review.

### Evidence-backed review resolution and remaining source correctness

- Replied to each of the eight fixed/superseded review threads separately with
  immutable source/test citations and resolved only those evidenced findings.
  The bounded fresh-review wait returned current-head review `5636717318` on
  `e86990aacbf71667d0c323055c1364984672632f`: COMMENTED, independently confirming
  the eight resolutions and identifying three further actual blockers.
- Fixed the terminal-row separator boundary in both compatibility and resident
  writers. They retain terminal-row length from the existing bounded scan and
  refuse an append whose separator would make that row exceed 64 KiB. Existing
  matching maximum-sized unterminated receipts still replay without mutation.
  No additional history scan, capacity widening or clock change was introduced.
- Fixed replay synchronization across rolling-log replacement: bounded resident
  history handles now remain open in an ExitStack through owner revalidation
  and fsync. Replay synchronizes the descriptor that produced its match rather
  than reopening a possibly replaced pathname. All handles close before guard
  release, including failure paths.
- Eleven selected functional Python units pass with lint, covering both
  writers at 65,535/65,536-byte terminal boundaries, maximum matching replay,
  actual replacement/blocked replacement at the post-match revalidation
  boundary, matching descriptor identity/closure, failed fsync, association
  refusal, actual activity-query durability and record-before-trace ordering.
  One additional focused unit confirms the retained matching descriptor closes
  when post-match owner revalidation refuses admission. These were not
  native/timing runs or performance acceptance.
- Fresh-state pending identity persistence remains open. The current scheduler
  retains it only in the passed state object; production recreates that object
  on extension startup. Existing handoff-request storage represents baton/
  consumption state and performs synchronous writes/directory creation, so it
  must not be silently repurposed as an observation journal. A selected existing
  session-owned persistence API/storage seam needs architecture admission
  before implementing durable receipt-pending state; no new ledger, fabricated
  session directory or test-only mock is claimed as a production solution.
- The prematurely reserved quiet window was cancelled before admission. Other
  lanes are released, and no H2 native GO has been issued. The user now accepts
  a separately reported contended test tier starting at 2x, at most 3x on
  explicit continuation; there is no mandatory zero-CPU condition. The
  nominal performance targets and production defaults remain unchanged, and
  the test-only factor mechanism is still a proposal, not an applied override.
- Original historical evidence remains unchanged. Candidate source changes
  invalidate use of the earlier prepared e869 manifest as proof for a newer
  head; fixture manifests must be regenerated before any future admitted run.
  Readiness, draft/merge, source CI/review and installed/platform gates remain
  separate; cancellation and scaled-test intent are not completion.

### Native metadata owner selected and admitted

- The coordinating owner selected the actual host SDK's persisted private
  client-metadata bag. Read the current 1.0.101 SDK declarations directly through
  the extension authoring guide: native metadata snapshot, get and atomic
  update return values, local/private semantics and all bag limits.
- The API has no CAS. The admitted protocol is three commutative immutable
  identity-bound records for one logical first-prompt slot, with retained
  monotonic confirmation and no clear/remove/foreign writes. The precise
  owner/API decision and source/native validation gates are in
  [native-metadata-contract.md](native-metadata-contract.md).
- Implementation scope is the H2 pending-slot adapter, scheduler/resident
  before-send gate, production registration wiring and focused tests/docs.
  P-owned files, handoff-request storage, services, CLI/session creation,
  provider registration and extension reload remain excluded.

### Native metadata source implementation and qualification boundary

- Added the private native API adapter and production registration wiring.
  Restore precedes observation; qualification is committed/read back before
  record admission, with monotonic confirmation and the original whole budget.
  Unsupported hosts and inconsistent storage never fall back to in-memory
  persistence. Source/nonce/route projection is revalidated after a waiting
  metadata commit.
- Added checked-no-recovery captures, retained terminal markers, no-CAS
  interleavings, stale read-back, late/disposed RPC ownership, actual native SID
  selection, changed projection during commit, and known-memory/native-record
  loss regressions. A fresh capture-only restore cannot choose another binding
  after failed qualified persistence; without the original tuple it remains
  unavailable instead of fabricating an exact query or new record.
  Forty-nine adapter/production-registration/scheduler
  source units passed; the native driver parsed without being executed.
- Independent read-only review of the material metadata protocol delta found
  no significant issues, without rerunning tests or claiming host persistence.
  The subsequent conservative capture-only refusal and its new regression
  passed the full 49-case source group; current-head forge review is still due.
- The latest selected client-only functional run passed six of seven tests.
  Lost-reply replay returned `unknown` instead of expected `logged`; its
  two-attempt case took 2,530.9407 ms. The preserved result does not identify
  the internal refusal reason, so timing causation is not asserted. A previous
  selected seven-case run passed, but that does not erase this current failure.
  No unchanged timing retry, production budget change or native run followed.
- These tests use an explicitly labeled RPC-owner double. Actual SDK host
  persisted atomic commit, process/refork restoration and independent runtime
  writer interleavings remain unqualified. The native slot is reserved, not
  GO, until the repaired source/current review/CI and the test-only mechanism
  are admitted. No extra native session/CLI host or extension reload occurred.
- Pre-protocol missing state cannot reconstruct previously lost identity or
  historical first-message order. Multiple logical handoff episodes under one
  native session ID are refused, not implemented as another ledger. This
  initialization boundary remains explicit for coordinator acceptance.

### Current-source test preparation, no native execution

- The metadata implementation published at `b12982076` passed source CI and
  current-head Copilot review5637716402 with no open issues. All review threads
  were resolved without clearing the separate native/installed/platform holds.
- Added a test-local factor hook with default1 and exact `1|2|3` spelling.
  It validates matching explicit client/provider experimental time constants
  before resident construction and scales only test wall-clock guards and
  monkeypatched in-process lock/scan/pre-effect budgets. Production sources,
  limits and defaults remain unchanged; factor3 is never an automatic retry.
- Actual elapsed/callback measurements remain unscaled, with separate nominal
  and experiment budgets/outcomes. Negative scan delay stays above the active
  scan budget; polling cadence and semantic resource/custody/cleanup assertions
  do not change. The native case inventory remains33.
- Pure factor/fixture-contract checks and the existing driver's syntax/argument
  smoke mode do not construct a resident, import the observer, invoke a backend,
  connect to an SDK host or create a native session. Preparation is not native
  execution permission or evidence of passing the retained failed gates.

### Accepted bounded factor2 source-fixture qualification

- The integration owner accepted the single full33-case run at
  `8f0b0f30ec570f4abf2fc1ab65e377f13607fd83`, with metadata production bytes
  from `b12982076b6534537f01f459c4227fd3612ee2c8`. All33 exact prepared case
  names passed, including teardown, with no skip/error or subset retry.
- All51 captured observations retained actual monotonic measurements and were
  below the experiment's4,000ms ceiling; two were at least2,000ms and the maximum
  was3,851.874ms. This is **factor2 acceptance, not nominal-SLA acceptance**.
  Helpers were zero and maximum in-flight work was four. The runner exited0,
  streams closed, and the unchanged fixture teardown asserted settled backend
  custody, zero active work and no remaining fixture server/handler threads.
- The51 records comprised49 explicit transport-fixture gates and two
  SDK-shaped RPC-owner doubles. No actual SDK-host/refork, whole hook-dispatcher,
  installed deployment or platform-wide qualification follows. No factor3 run
  was needed or authorized; no readiness marker or rollout was added.
- The owner independently verified the closed raw hashes, original preparation
  bytes, exact selectors and observations. Five generated provider Python-cache
  files were disclosed and preserved. The accepted22-member archive is
  `h2-native-factor2-20261024.zip`, SHA-256
  `78ac793064349481a3fc8dd087a456d756f5cba2dc069007897297cc557a3a48`.
  Historical failed evidence remains retained; the native slot was released.
- Publication/review of preparation source must use its current head, not the
  older b129 review. The PR remains draft/no-merge while the distinct actual
  SDK-host and remaining composition/installed/platform gates stay open.

### Incremental source-only acceptance

- The integration owner released the source-only merge hold for this defensive
  zero-helper slice. Accepted evidence is the full33-case factor2 real
  ControlServer/backend/Node fixture,49 metadata adapter/registration/scheduler
  units, current required CI and an actual current-head clean review.
  This permits incremental source publication, not bypassing a concrete
  correctness/safety defect, unresolved finding or failing required check.
- The earlier draft/no-merge instructions document prior qualification phases;
  they no longer require this source PR to wait for every campaign objective.
  Normal ready/review/CI/role-scoped managed merge gates still apply.
- Remaining objectives stay explicitly open under issues #7942 and #3303 and
  the coordinating campaign: actual SDK-host/refork storage and creation-time
  isolation, whole hook-dispatcher composition, nominal2,000ms behavior, other
  platforms and installed deployment. Those are named follow-ups, not claimed
  passes or automatic blockers to this scoped source slice.
- No ready-capability marker, production deadline change, new host/session/RPC,
  extension reload, global service/update or rollout is authorized by this
  source-only acceptance. The broader effort remains active and is not Done.
