# Optional built-in Sync progress

- **Parent:** [Worktree Manager control plane](README.md#optional-built-in-lifecycle-progress).
- **Status:** Provider expansion implemented and source-tested; review pending.
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

Current `maintenance.build_tasks` invokes local and remote terminal-JSON Sync;
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
- [ ] **S1 - Provider expansion:** Add optional stage callbacks inside the
  existing Sync owner and the strict CLI opt-in/packaged declaration. Preserve
  paired admission, both-member preflight, safe fast-forward, composition order,
  policy reload and all original failure results. No alternate Sync engine.
- [ ] **S2 - First real consumer:** Qualify the exact local engine generation,
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

The qualified Windows x64 source packet passes 33 distinct cases in disjoint 10/10/13
selections, including all 32 Sync cases and the existing fetch-ledger control.
The ordinary 30-second item and 300-second group bounds remain unchanged.
This is not a monolithic aggregate-budget or native ARM64 qualification.
Focused F/E9 lint, installation-contract and changed-module-size checks pass.
Exact-head review/CI still gate publication acceptance.

S2 local incremental rendering, selected-runtime negotiation/rollback, consumer
two-clock and uncertain-outcome validation remain open. No Manager path uses the
new flag yet. Native/installed and external-venue qualification were not run:
this producer increment changes no installer or live host, and no qualified
live venue or rollout is admitted. No installed behavior is claimed.

## Validation Plan

- [ ] **SV1 - CLI compatibility:** Real parser/process tests prove default text
  and JSON behavior remains unchanged, unknown options remain strict, the opt-in
  emits flushed frames and its complete terminal result matches ordinary Sync.
  Cover a successful no-op and one real blocked/partial pair, not a synthetic
  percentage-only producer.
- [ ] **SV2 - Incremental user flow:** A contained temporary Git/pair fixture and
  the production maintenance/ProgressScreen path prove a real stage message
  appears before a deliberately held operation completes, the UI stays usable,
  and final pair progress/refusal diagnostics remain visible. Release the gate
  and settle the operation; do not infer incremental consumption from final text.
- [ ] **SV3 - Selected-runtime skew:** New consumer/current provider, legacy or
  missing declaration, invalid/out-of-range declaration, changed selection and
  supported rollback choose the intended path before effects. Existing callers
  against the new provider keep their original result and no stream is retried.
- [ ] **SV4 - Failure/lifetime:** Missing terminal, malformed/non-object frames,
  contradictory terminal/exit, stderr pressure, timeout and view dismissal do not
  report false success, rollback or safe replay. Preserve partial results and
  exact child cleanup; no background reader outlives its owned run.
- [ ] **SV4a - Two clocks:** Healthy nested progress outlasts the communication
  interval; unfamiliar updates and owner keepalives renew contact, local timers
  do not. Initial/inter-phase silence preserves uncertain execution without
  replay. The independent execution budget still expires despite continuing
  updates, with the original operation identity and exact cleanup retained.
- [ ] **SV5 - Scope and publication:** Focused owner/Manager suites, lint and
  mandatory repository checks pass on the final source. Record native/platform,
  release and installed tiers separately, with actual blockers or reasons for
  unexercised lanes. No host install or live worktree Sync is part of a source
  test. The complete #4183 issue stays open for its remaining original lanes.

## Journal

### 2026-11-10 - Producer validation and refinement

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
