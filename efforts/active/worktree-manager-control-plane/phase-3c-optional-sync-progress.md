# Optional built-in Sync progress

- **Parent:** [Worktree Manager control plane](README.md#optional-built-in-lifecycle-progress).
- **Status:** Provider/consumer source accepted; complete CLI comparison qualified, awaiting review.
- **Source:** [#4183](https://github.com/ThomasMichon/copilot-extensions/issues/4183)
  LP1-LP3/LPV, including the complete received #4274 request.
- **Participant:** Optional Sync-progress implementer, public source
  `patient-knob`. Other lifecycle, channel, input and runtime owners retain
  their scopes.

## Goal and scope

Let an explicitly invoked built-in Sync report real stage messages through the
existing D4 NDJSON envelope and existing Picker ProgressScreen. Preserve ordinary
CLI output, safe paired synchronization, complete results and the process
boundary. This is optional progress detail, not a responsiveness repair:
Phase 3c remains complete.

This agent-recommended first increment covers the Sync producer and the local
Manager maintenance consumer. Other lifecycle verbs and remote streaming remain
unchecked follow-ons under #4183. Existing remote Sync keeps its supported JSON
path. No installation, native session probe, SSH connection, runtime rollout,
configuration writer or custody transfer is authorized by this proposal.

## Governing intent and observed boundary

This closes part of the [Picker vision](../../../visions/picker/README.md):
`programmatic-parity`, `live-not-snapshot`, `graceful-capability-scaling` and
`launch-without-worktree-maintenance`. The native engine remains the authority
for synchronization and pair preparation; Manager renders, never imports it.
Follow [version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and [progress and timeout semantics](../../../docs/patterns/progress-and-timeouts.md),
and the [engine/Picker boundary](../../../plugins/agent-worktrees/docs/engine-picker-contract.md).

Before this increment, `maintenance.build_tasks` invoked local and remote terminal-JSON Sync;
`MaintenanceExecutor` already runs it off-thread and retains each result.
`worktree_ops_cli.cmd_sync` fetches, observes and synchronizes; `sync_pair`
validates both members before advancing them and prepares local plugins only
after success. Those stages are real progress sources, not simulated timing.

`tasks.run_action_stream` establishes the D4 shape:
`progress` with optional `pct` and `msg`, then `done` or `error` with `message`.
Its generic string return discards structured results; its non-envelope fallback
and cancellation behavior do not establish the built-in Sync contract.
Reuse that wire shape and ProgressScreen, not a registered-pivot runtime,
PATH-based command selection or a claim that cancelling display rolls back Git.

## Contract and compatibility

- Add an explicit Sync-only `--stream` opt-in alongside `--json`; reject
  unsupported combinations before effects. Without it, existing syntax, stdout,
  stderr, result objects and exit behavior remain unchanged.
- Emit and flush D4 `progress` frames at actual stage boundaries. Prefer
  indeterminate `pct` to invented percentages: repositories, network fetch and
  plugin preparation have unequal work. Messages describe stages, not successful
  completion of a stage still running. The supervising owner also reports its
  current waiting phase during long nested work, without fabricating child
  advancement or launching a process per event.
- Terminal `done`/`error` frames add a `result` property containing the complete
  original JSON result, including `pair_results`, `plugins` and failure detail.
  This is an additive operation-specific payload in the same envelope; existing
  contributed D4 readers may ignore it. Do not truncate results into a message.
  Success must mean the same completed Sync that the current maintenance
  consumer accepts, not merely exit zero or an earlier member's `updated`.
- The built-in consumer requires a valid terminal result and consistent process
  outcome. EOF, timeout, malformed output, lost transport and partial Git
  progress are not confirmed success and never trigger replay. Preserve useful
  diagnostics without inventing a second result/progress store.
- Adopt independent short communication and long execution budgets for the
  optional stream, including initial silence. Every received target update,
  even an unfamiliar event, renews contact; a local UI tick does not. Silence
  reports observation loss, not cancellation or execution failure. Do not retain
  the old JSON wrapper's fixed 120-second deadline around a healthy longer stream.
  Execution expiry follows owned teardown, retains partial/unknown outcome and
  reports which clock expired with the last observed stage.
- Expand the provider first, with one versioned Sync-progress support declaration
  packaged in its immutable runtime slot. Select it from the exact attributed
  command that will execute, not package versions, a newer payload or PATH.
  Missing, invalid or unsupported metadata selects the unchanged JSON baseline
  before effects. No new capability-probe subprocess or universal handshake.
- Migrate only the qualified local consumer initially. A supported provider
  rollback retains JSON Sync; no optional-flag stripping or retry after a started
  invocation. Remote targets remain on their existing attributable JSON path
  until their own selected-runtime support can be established.

## Plan

- [x] **S0 - Review:** Accept this bounded proposal and participant before code.
  Keep the original #4183 LP acceptance open and preserve PK/WI owners.
- [x] **S1 - Provider expansion:** Add optional stage callbacks inside the
  existing Sync owner and the strict CLI opt-in/packaged declaration. Preserve
  paired admission, both-member preflight, safe fast-forward, composition order,
  policy reload and all original failure results. No alternate Sync engine.
- [x] **S2 - First real consumer:** Qualify the exact local engine generation,
  consume frames incrementally off the UI thread, retain the terminal result in
  the existing executor and render its latest message in ProgressScreen.
  Preserve per-item ordering, source/selection identity, bounded process ownership
  and existing complete paired diagnostics. No new persistent worker.
- [ ] **S3 - Acceptance:** Record actual source proof and merge under normal
  review/CI. Document the optional CLI/consumer contract and mixed-version
  behavior as reality only after implementation. Retain other verbs, remote
  streaming, release and authorized installed qualification explicitly.

Closing a progress view must not imply rollback, successful cancellation,
settled operation or permission to replay. Do not wire Sync into the generic
D4 kill-on-Cancel path. Keep the existing maintenance operation's ownership with
an explicit finite stream execution budget; drain stdout/stderr concurrently and settle exact owned
children using the existing process owner. Cleanup failure remains visible.

## S1 implementation and source validation

The explicit `sync --json --stream` producer reports real Sync stages and
owner-emitted waiting messages in D4 frames. Terminal frames retain the whole
native result, including partial pair outcomes and preparation failures.
Ordinary CLI calls take the original result path without a heartbeat or stream.
The version-1 declaration is included in the engine's package data.

Sync requires exact option names,
retains native selector diagnostics in a known-not-started terminal refusal,
and preserves completed entries plus an explicitly unknown interrupted item.
The command body lives in the native Sync module; its legacy CLI entry and
native helper seams remain available. Private test fixtures copy one fresh seed
into independent repositories and inspect actual loose refs, retaining real Git
synchronization, independent pair storage, failure assertions and cleanup.

Windows x64 source qualification covers 37 distinct cases: 14 stream/current-owner
controls and 23 default/pair/freshness controls. Evidence and reuse scope are
recorded in the [dated Journal](#journal).
The ordinary 30-second item and 300-second group bounds remain unchanged.
This is not a monolithic aggregate-budget or native ARM64 qualification.
Focused F/E9 lint, installation-contract and changed-module-size checks pass.

The S2 local consumer now qualifies the exact engine command before selecting
the optional flag. Missing, invalid and unsupported metadata retain the pinned
JSON invocation; remote Sync remains JSON. The existing executor and native
ProgressScreen retain per-item identity, live messages and complete pair results.
Communication silence is observed separately from the finite execution budget.
Escape preserves an active Sync rather than claiming cancellation or rollback.
Unconfirmed cleanup retains exact process custody and stops subsequent items.

Source qualification covers 74 distinct Manager controls, including real parser,
private Git pair and Textual consumer cases for a no-op and blocked partial pair.
The local source implementation is accepted in #11914. Complete default-versus-
streamed CLI result comparison (SV1) is now source-qualified; its reviewed
acceptance, final source-validation disposition and
independent native/installed/release qualification remain open. No host
installation or live worktree Sync is claimed.

## Validation Plan

- [ ] **SV1 - CLI compatibility:** Real parser/process tests prove default text
  and JSON behavior remains unchanged, unknown options remain strict, the opt-in
  emits flushed frames and its complete terminal result matches ordinary Sync.
  Cover a successful no-op and one real blocked/partial pair, not a synthetic
  percentage-only producer.
- [x] **SV2 - Incremental user flow:** A contained temporary Git/pair fixture and
  the production maintenance/ProgressScreen path prove a real stage message
  appears before a deliberately held operation completes, the UI stays usable,
  and final pair progress/refusal diagnostics remain visible. Release the gate
  and settle the operation; do not infer incremental consumption from final text.
- [x] **SV3 - Selected-runtime skew:** New consumer/current provider, legacy or
  missing declaration, invalid/out-of-range declaration, changed selection and
  supported rollback choose the intended path before effects. Existing callers
  against the new provider keep their original result and no stream is retried.
- [x] **SV4 - Failure/lifetime:** Missing terminal, malformed/non-object frames,
  contradictory terminal/exit, stderr pressure, timeout and view dismissal do not
  report false success, rollback or safe replay. Preserve partial results and
  exact child cleanup; no background reader outlives its owned run.
- [x] **SV4a - Two clocks:** Healthy nested progress outlasts the communication
  interval; unfamiliar updates and owner keepalives renew contact, local timers
  do not. Initial/inter-phase silence preserves uncertain execution without
  replay. The independent execution budget still expires despite continuing
  updates, with the original operation identity and exact cleanup retained.
- [x] **SV5 - Scope and publication:** Focused owner/Manager suites, lint and
  mandatory repository checks pass on the final source. Record native/platform,
  release and installed tiers separately, with actual blockers or reasons for
  unexercised lanes. No host install or live worktree Sync is part of a source
  test. The complete #4183 issue stays open for its remaining original lanes.

## Journal

### 2026-11-10 - Complete CLI comparison source-qualified

- Four focused native-owner controls pass: strict parser, existing complete
  missing-record JSON envelope plus unchanged text output, and two new real
  parser/process comparisons for no-op and blocked/partial private pairs.
- Each new case executes ordinary and streamed CLI composition in one owned
  child against the same temporary Git pair. The partial case restores only
  its private harness ref/index/file before the second invocation, preserving
  equivalent starting state and actual Git advancement. Entire terminal
  objects compare equal, without dropping or normalizing fields. Exit status,
  frame type, version, member/plugin outcomes and real refs are also asserted.
- The source packet passes in 33.92s, 135.26s including preparation/cleanup;
  pair cases take 18.16s and 14.99s. The child bound is 25s inside unchanged
  30-second item/300-second group bounds. Existing Manager qualification is
  unchanged and not rerun by these test-only additions.
- SV1/S3 remain open pending the validation follow-up's normal review/CI/merge.
  No product behavior, installer, live Sync, native session probe or rollout
  changed. Source acceptance does not qualify installed or other-platform use.

### 2026-11-10 - Local consumer source accepted; CLI comparison next

- #11914 merged at 11:45:56 UTC as
  `f6446aba1b8b83fb2f78ea2683c6f4b3a3305244`. Genuine Copilot approval
  `5841218922` covered exact head `7eec4ef83035892e2caace69430f8637b69cb0b3`;
  its complete body reports no active issues and no new inline findings.
  All four original threads are resolved. Author-side empty inline-reply
  review records were not counted as bot verdicts.
- Exact-head CI `42808788629` passed; the later metadata-triggered run
  `42808871434` also completed successfully, including its required PR gate,
  before the managed merge. Source attribution and lockdown checks passed.
  Only the provider-authorized extra CODEOWNER vote was excepted; no finding,
  thread or CI was bypassed. The complete #4183 tracker remains open.
- Post-merge source sync safely auto-aborted a squash-history conflict.
  Native completion preview verified exactly three accepted commits and no
  post-merge work; reconciliation retained a backup and advanced onto the
  actual merged source. The only incoming Manager difference was unrelated
  bootstrap-test maintenance. Subsequent native sync succeeded. No held
  workspace was finalized.
- S2 and source-tier SV2/SV3/SV4/SV4a/SV5 are accepted at the recorded actual
  Windows x64/controlled-runtime scope. Native POSIX, ARM64, desktop/installed
  and release evidence are not inferred. SV1 still needs full ordinary-versus-
  streamed result equality for both no-op and blocked/partial pair CLI paths,
  beyond existing error-envelope comparisons and field assertions. Keep S3
  and the parent's validation row open until that exact oracle is qualified.

### 2026-11-10 - S2 review refinements

- The second genuine review identified the existing local-source UI test
  double's missing cancellation argument and an unintended 15-second teardown
  wait on the unchanged remote SSH adapter. The test double now accepts the
  lifetime argument and explicitly keeps its JSON-only fixture, preserving
  stale-configuration and published-local-identity assertions.
- Generated tasks identify local Sync ownership. Teardown stops queued work,
  joins an active owned local Sync, and leaves an active remote SSH command on
  its existing bounded JSON lifetime without waiting, cancellation or a false
  settlement claim. Remote-only and both mixed-order selections have focused
  controls; no SSH command or new remote owner is introduced.
- All 45 affected controls pass, including the twelve existing stale/invalidated
  local-source identity cases, three remote-only/mixed-order teardown controls,
  four real application exits and both real private-pair flows. The 29 unchanged
  passing controls retain their preceding qualification, giving 74 distinct
  outcomes without claiming a monolithic rerun. Labels for Windows/WSL/Linux
  in the identity fixtures are synthetic on the actual Windows x64 test host.
- #11914's first exact-head review identified two real blockers: application
  exit could leave the one-hour operation without a visible screen, and
  retirement failure could discard an already received terminal result.
  Neither finding was dismissed.
- Picker teardown now requests executor stop before provider disposal, settles
  the current owned local command and joins its worker/observer even if another
  provider's disposal fails. Queued items are not started. The legacy JSON
  fallback retains its syntax and execution budget while using the same owned
  lifetime when invoked by the application. Escape remains non-cancelling.
- Stream and baseline cleanup exceptions carry their observed result and
  bounded stderr into the existing executor, alongside exact native custody
  and retained output files. Overall status is unknown/cleanup-pending; prior
  member progress, plugin state and refusal diagnostics are not erased.
- The affected packet passed 55 named controls, including both real Git-pair
  flows, two terminal-then-cleanup-failure regressions and existing provider
  teardown controls. Four new application-lifetime cases initially exposed
  fixture defects, not waived outcomes: incomplete first-paint/progress state
  and an unconditional fake-provider loop that confused read and Sync receipts.
  The fixture now uses actual first-paint chrome and the production progress
  entry, with operation-qualified private child evidence.
- All four corrected real PickerApp idle/owner exit cases pass for current
  stream and legacy JSON, with settled workers/observers, stopped child
  heartbeats, unchanged first-item identity, unknown interruption and no later
  item invocation. Together with the 55 unchanged passing controls, this is a
  59-case source union, not a full rerun after fixture/legend-only correction.
  Item/group bounds remain unchanged. Required new-head review and CI still
  gate acceptance; no runtime deployment occurred.

### 2026-11-10 - S2 local consumer source qualification

- Implemented exact-command support selection, incremental D4 observation,
  complete terminal/exit agreement and existing ProgressScreen integration.
  The selected command is pinned for the invocation; unsupported generations
  retain JSON before effects. No started stream is replayed.
- Kept a 30-second communication observation and an independent one-hour
  execution budget. Initial/inter-phase silence is visible without killing the
  operation; unfamiliar updates and optional-message progress renew contact.
  Execution expiry, malformed/absent terminal results and output-budget failure
  preserve unknown outcomes, last stage and diagnostic detail.
- Reused the existing engine Job/group owner with file-backed stdout/stderr
  draining. Its optional Windows cleanup confirmation retains exact failed
  custody. The shared owned-process `output_fds` seam is POSIX-only; no parallel
  process framework, shared-library extension or new persistent worker was added.
- The standard contained Windows x64 packet passes 49 cases, without extending
  the 30-second item or 300-second group bounds. The two real private-pair flows
  take 15.07s and 12.37s: both render a real fetch stage before release, continue
  Textual ticks/keyboard handling while held, and retain the final result.
  Additional controls cover selected-runtime rollback, legacy/remote baseline,
  malformed/contradictory output, stderr pressure, independent clocks and exact
  descendant/cleanup behavior. Synthetic POSIX ownership controls are not native
  POSIX qualification.
- The final unknown-outcome rendering change passes all ten affected screen
  controls, including its new uncertain-partial-result case. Together with the
  40 unchanged controls retained from the preceding 49-case packet, this is a
  50-case union, not a claim that the entire packet was rerun after that
  diagnostic-only change.
- Cleanup failure retains the output directory as well as native process
  custody, so failed file retirement cannot mask the original exception.
  The confirmed POSIX stream keeps its existing unreaped group owner inside
  test containment; default engine preparation is unchanged. The screen also
  names unknown Sync outcomes explicitly rather than relying on a failed glyph.
- Fixture failures were corrected rather than waived: nested child quoting,
  a redundant pre-stream Sync, clean no-op index preparation and the actual
  native `plugins: skipped` failure value. Real Git/Sync remained in the flow,
  and neither item/group deadlines nor product assertions were weakened.
- Focused lint, the installation contract and all 41 admitted strict checker
  projects pass. The checker used fresh, hash-locked private test environments
  after the default cache reported missing prerequisites; no shared cache or
  host runtime was repaired. Manager's pending type-check scope is not thereby
  qualified. Required exact-source
  review/CI still govern acceptance. No installer or first-provision behavior
  changes; native ARM64, desktop, installed and live external lanes were not
  exercised and are not inferred from source tests. No qualified external
  development venue or rollout is part of this bounded source increment.

### 2026-11-10 - S1 producer source accepted

- #11889 merged to `dev` at 09:31:30 UTC as
  `0b3c3304e750ed6df153d3c52a113beb14ab62d4`. Genuine Copilot approval
  `5839544987` covered exact head `613e44f73c31181a7da3d95722e47e5075591b83`
  with no active issues. All five inline threads were resolved; the body-only
  schema/tracking findings were addressed, not inferred clean from a summary.
- Exact-head CI `42790051375` and the later required-gate producer
  `42790195698` both passed, with required metadata checks successful before
  the managed merge. The parent #4183 remains open.
- Supported post-squash reconciliation retained a backup, verified the merged
  published head and absence of post-merge commits, and advanced the source
  past its five accepted commits. Native synchronization succeeded. Unrelated
  incoming Create/Run ownership changes were preserved.
- Only S1 is accepted. S2 local runtime-qualified incremental rendering,
  S3 complete-flow acceptance, all Validation Plan items and independent
  release/native/installed gates remain open. No live Sync, installation,
  session probe or rollout ran.

### 2026-11-10 - Producer validation and refinement

- A standard-library buffered-stream reproduction showed that remembering a
  failed flush does not remove queued bytes: shutdown can attempt them again.
  The focused case failed before repair. Frames use the shared unbuffered
  single-attempt stdout writer, and a failure, including its terminal signal,
  is retained across the heartbeat/main thread boundary. Shutdown flushing
  has no queued frame, and later stages cannot write after failure.
  All 14 affected stream/current-owner controls pass; the 23 unchanged
  default/pair/freshness outcomes retain their qualification.
- Three additional regressions demonstrated a missing canonical schema version,
  an unframed non-mapping tracking refusal and a second attempt after an
  unconfirmed terminal write. All failed before correction. Terminal results
  use the shared ordinary-JSON formatter; the stream boundary reports ordinary
  preflight exceptions, while default CLI handling is untouched. The progress
  writer remembers a failed write and rejects explicit subsequent frame writes.
- All 13 affected stream/current-owner cases pass on the corrected source.
  The 23 default/pair/freshness cases retain the preceding passing qualification:
  their executed paths and fixtures are unchanged by the stream-only formatter,
  writer and exception-boundary changes. This is a 36-case independent union,
  not a claim that every case was rerun or that a monolithic budget passed.
  Mandatory exact-head CI/review remains required.
- Four focused tests failed before implementation for missing frames,
  pre-configuration syntax refusal and parser support. The first submitted
  source packet passed 30 distinct cases in disjoint 7/10/13 selections.
- A broad packet exhausted its group bound without named coverage; those dots
  were not reused as passing evidence. Named follow-up located item cost in
  repeated Git fixture/oracle children. Copying one fresh seed into independent
  repositories and inspecting actual loose refs reduced setup/assertion cost
  without replacing real synchronization or raising deadlines. The blocked
  oracle also had to retain the native distinction between top-level
  `updated: false` and earlier successful member results.
- ARM64 dependency preparation required a cryptography source build without an
  available Rust toolchain. Source validation used an existing x64 interpreter,
  preserving dependencies and host configuration. A retained-cache access
  refusal was preserved; the runner's supported private scratch selection
  supplied a fresh owned cache.
- Unsupported shortened options must not start a mutation; known selector
  refusals need terminal diagnostics rather than an unknown stream; and a late
  batch exception must not erase completed effects. Four focused regressions
  demonstrated those failures before correction. Strict parsing, captured native
  selector diagnostics and retained batch results address those invariants.
  Moving command composition into the existing Sync owner also restores the
  CLI module's 1000-line cap without widening a baseline or dropping its entry
  and helper compatibility seams.
- The revised source packet passes 33 distinct cases in disjoint 10/10/13
  selections under the same item/group bounds. Focused lint, installation
  contract and changed-module-size checks pass. This remains source validation,
  not Manager consumption, native ARM64, release or installed acceptance.

- 2026-11-10: #11873 merged to `dev` as
  `431751d0fd5d916bdd1132a32f82652431a26ac5` after genuine Copilot approval
  `5837642164` on exact head `1f246476398d01c4a8744cde91ccad715539146c`,
  no inline findings and all required checks passing. Native source sync
  succeeded. S0 is accepted; S1/S2, all implementation validation and the
  original #4183 acceptance remain open. No runtime operation occurred.
- 2026-11-10: Qualified the existing D4 envelope, real Sync stages, current
  terminal-result consumer and selected-runtime support pattern. Proposed the
  bounded provider-first/local-consumer sequence; no implementation or runtime
  effects claimed.
