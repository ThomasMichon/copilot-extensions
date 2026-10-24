# Discoverable diagnostic logging and 30-day retention

Parent: [Linux role fleet](README.md). Integration: #8377. Standing intent:
[convention-discoverable diagnostic logs](../../../visions/machine-fleet/README.md#convention-discoverable-diagnostic-logs).
This records the reviewed plan and bounded source progress, not a running-state
or whole-fleet retention claim.

## Operator direction and scope

The operator requested an audit of existing logging, predictable convention-based
discovery, timestamps, attribution and 30-day retention. The parent's Request
amendment preserves the literal wording.

Cover adopted Gateway/Fleet control-plane components: application and daemon
output, controller/connectors, service supervisors, passive/cutover/recovery
processes, native lifecycle/boot/reconciliation, installer attempts, container
output, session diagnostics and optional structured diagnostic buckets.
Authoritative task/session databases, ownership receipts and operator-authored
artifacts are not log files to expire through this policy.

## Source-backed audit baseline

The initial audit examined the merged source containing #8334. Later source
changes and adopter deployment settings must be reconciled before implementation.
The entries below identify real writer seams, not successful live adoption.

| Category | Existing owner and discovery | Existing policy / gap |
|----------|------------------------------|-----------------------|
| Bridge daemon stdout/stderr | `plugins/agent-bridge/src/agent_bridge/service_process_cli.py`, `_launch_bridge_daemon`; runtime-root `agent-bridge.log` and `agent-bridge-err.log` | Native append captures; no elapsed-age retention or universal per-record attribution. |
| Dispatch coordinator/supervisor | `plugins/agent-dispatch/src/agent_dispatch/logging_setup.py`, `configure_file_logging`; runtime-root `logs/` | Timestamp/level/logger formatting; 5 MB and three-backup rotation is not 30-day retention. |
| Dispatch passive/self-deploy output | `coordinator_cli.py` and `coordinator_loops.py` select `logs/coordinator-generation.log` and `logs/self-deploy.log` through the native launcher | Separate append captures; application-handler rotation does not cover them. |
| Native lifecycle | `libs/zdd/src/zdd/lifecycle.py`, `record`; config-root `lifecycle.log` | Structured stage/outcome records; 1 MB rollover with one backup is not elapsed-age retention. |
| Daemon process capture | `libs/agent-procutil/src/agent_procutil/daemon.py` | Timestamped launch header, but inherited output handles remain open; no common age-retention owner. |
| Installer attempts | `libs/installer-engine/installer_engine.py`, `run_owned`, plus native engines and installer entry points | Captured console output alone is not durable evidence. #8370 owns independent attempt capture/discovery/retention. |
| Boot/reconcile and fallback files | Native shim/bootstrap and per-plugin installer owners | Include alternate version/PID log names and early bootstrap output; account for these separately from application handlers. |
| JSONL telemetry | Bridge/Dispatch `telemetry.py`, `make_spool_sink`; `libs/process-telemetry` append primitive | Optional configured paths and producer-shaped records; #8334 preserves concurrent records but does not rotate or expire them. |
| Session child diagnostics | Bridge ACP/session-host owners and existing event storage | Bounded stderr/event counts do not establish age retention; classify diagnostic records separately from authoritative conversation state. |
| Gateway/Fleet/container logging | `fleet-manager/src/fleet_manager` and the selected role/deployment adapter | Module/privacy-aware loggers need an explicit deployment sink/discovery owner. Audit adopter app-file and container output mappings; a Docker size limit is not an age policy. |

Adopter-specific sources and live inventories remain downstream. They extend
this inventory without publishing private paths, identities, credentials or
raw diagnostic captures here.

## Proposed realization

The following implementation details are **agent-recommended**:

1. Extend existing runtime/config-root resolvers and diagnostic commands with
   one discoverable log-location inventory. Preserve current paths/consumers
   or provide explicit compatibility discovery; do not invent another registry
   of machine identities or a parallel collector. Include native/container
   destinations, historical segments, alternate fallback paths and missing or
   degraded logging, even when an installer could not announce its path.
2. Reuse existing event shaping, privacy-aware loggers, lifecycle writer and
   process-capture seams. Records expose UTC timestamps, component/service,
   process/instance and runtime/generation attribution, and operation/correlation
   when actually available. Missing evidence is explicit, never fabricated.
   Preserve clean JSON stdout and primary/native errors.
3. Apply a default 30-day elapsed-age policy to diagnostic records through
   each owning writer. Roll continuously active output into bounded,
   time-identifiable segments; age cleanup must run on normal owner maintenance
   and startup, not only on the next log message. Thirty backups or deleting
   old filenames while a forever-active file grows is insufficient.
4. Coordinate append/rotation across concurrent writers and Windows open
   handles. Do not truncate an active file or move new records into an
   untracked renamed file. Preserve native process lifetime/containment and
   bounded stdout/stderr draining; a logging failure reports degraded evidence
   without replacing the primary operation result.
5. Compose #8370 rather than reimplement its installer-attempt owner. Its active
   and incomplete attempts are protected until that owner settles them;
   discovery must expose the protection and age explicitly. Diagnostic retention
   never expires ownership receipts or authorizes cleanup of running work.
6. Distinguish logs from telemetry coverage. Preserve candidate-health,
   routing-commit, drain, retirement, rollback and final serving-verification
   evidence. A healthy HTTP response, replayed activity sample or generic
   `update failed` line cannot stand in for the actual failed boundary.

## Plan and validation

- [ ] Reconcile this inventory with current source, selected role closures and
  adopter/native/container output mappings; assign every writer to its owner.
- [ ] Review the shared convention and compatibility/retention contract before
  changing writers. Keep #8370/#6812 and existing lifecycle/release owners.
- [ ] Implement discovery and attribution through those owners, then age
  retention and native output adapters in independently reviewable slices.
- [ ] Test exact 30-day cutoff, UTC boundaries, clock skew, continuously active
  logs, no-message/idle restart, stale archives and protected incomplete attempts.
- [ ] Exercise real multi-thread/process writes and native Windows/POSIX open
  handles through rotation; prove complete, non-interleaved retained records.
- [ ] Validate real consumer discovery of active/rotated/fallback/container logs
  without depending on a prior terminal or hardcoded machine path.
- [ ] Inject pre-commit failure, rollback and failure-after-commit; correlate
  attributed stage evidence with actual selected/running generations.
- [ ] Prove privacy and clean protocol stdout on early/bootstrap, standalone,
  staged, timeout/interrupted and retained-child installer paths with #8370.
- [ ] Record unit, lightweight integration, fresh-environment and available
  native/live evidence for changed owners. An unavailable lane stays explicit.
- [ ] Publish source through review/CI, then separately qualify released and
  admitted running behavior. Do not mark the parent effort Done at plan merge.

## Dispatch application source slice (#8377)

`spinning-sprocket` takes only the Dispatch application-handler and local
discovery slice. Reconciliation confirmed #8370's independent installer capture
and #6812's progress presentation ownership; neither shared implementation is
modified. The source realization is described in
[Dispatch diagnostic logs](../../../plugins/agent-dispatch/docs/diagnostic-logs.md).
It closes part of the reviewed `convention-discoverable-diagnostic-logs` intent,
not the whole feature or any live deployment gate.

- [x] Reconcile the bounded application/native writer seams and preserve adjacent
  installer/progress ownership before editing.
- [x] Select an explicit application namespace so legacy/native inherited
  handles cannot be renamed/truncated by the new handler.
- [x] Implement and validate application UTC attribution, exact record-age
  retention, real concurrent writers and active/archived/fallback discovery.
- [x] Publish the bounded source through passing review and required CI.
- [ ] Integration owner: qualify release and authorized running adoption, and
  complete the remaining native/installer/Bridge/telemetry/controller/container
  inventory, owner adapters and failure-stage correlation gates above.

The broad Plan and validation items remain open: an application handler cannot
promise timestamps or retention for native stdout/stderr, early bootstrap,
installer attempts, optional spools or a deployment adapter.

### Journal

- 2026-10-23 — Implementation starts with Dispatch application diagnostics using
  its existing install resolver, operation lock, package logger, daemon GC and
  supervisor iteration. Read-only JSON discovery includes unmanaged legacy/
  native/fallback locations and missing files. No new collector or deployment
  operation; #8370/#6812/#8334 implementation seams remain untouched.
- 2026-10-23 — Native Windows source validation: 240 tests pass across the new
  diagnostics and existing coordinator/supervisor suites; the focused final
  diagnostics plus telemetry run passes 40 tests (13 diagnostics). This includes
  real concurrent process/thread Unicode rotation, exact cutoff/timezone/skew,
  continuous active output, forced idle/restart pruning, actual daemon
  maintenance before governance backoff, fresh read-only CLI discovery,
  fail-open replacement/initialization and excluded arbitrary LogRecord extras.
  Ruff F/E9, install-contract (13 plugins), documentation consistency and diff
  whitespace checks pass. These are application-handler tests, not native
  stdout/stderr framing or deployed service proof. Linux/macOS native lanes and
  live external qualification are not available in this source-only Windows
  scope; those gates remain open with the integration owner.
- 2026-10-23 — Source PR #8415 review identified abandoned prune copies,
  repeated full-history work and configured lifecycle routing discovery.
  Corrections reclaim only strictly recognized component temporaries under the
  same operation lock, cache validated unchanged-segment expiry information,
  rewrite only expired segments and yield custody between files, and resolve
  lifecycle locations through the actual routing-directory owner. Added restart,
  scan/copy work-count, cache-invalidation and routing-override regressions.
  Focused Windows diagnostics plus telemetry now pass 44 tests (17 diagnostics);
  unchanged Bridge concurrent spool append proof passes all 7 tests from #8334.
  No native capture, installer or live scope is promoted by these corrections.
- 2026-10-23 — Follow-up review correctly identified synchronous history work on
  coroutine logging. Emission now only appends/rolls the active file; existing
  off-thread coordinator GC and the supervisor iteration exclusively own ongoing
  history maintenance. The event-loop regression proves emission does no history
  scan even when expired archives exist, and the real GC regression asserts
  maintenance scans execute off the event-loop thread.
- 2026-10-23 — Re-review's previously missed interrupted-tail case is also
  addressed: check only the final byte while inspecting the active segment and
  roll an unterminated file before appending. A restart regression preserves the
  damaged archive byte-for-byte while proving the new event stays parseable.
  This does not alter the separately owned telemetry append contract.
- 2026-10-23 — Review identified the supported task-GC-disabled configuration.
  The existing lifespan-owned maintenance loop now remains armed for diagnostics
  in that mode, with a 60-second logging-only branch and no queue reconciliation.
  The actual application lifespan regression proves aged records expire while
  task GC remains disabled (zero liveness runs); shutdown retains the existing
  loop cancellation owner. No separate logging scheduler or process is added.
- 2026-10-23 — Final disabled-GC correction validation passes 282 native Windows
  tests across diagnostics, coordinator, supervisor, loop governance and telemetry.
  This preserves disabled task recovery, governance backoff and existing queue
  behavior while qualifying quiet diagnostic maintenance. The diagnostic module
  now has 20 cases, including the actual lifespan boundary.
- 2026-10-24 UTC — #8415 was approved at exact head
  `ad4c5f51645007438c1f26721e343c1b1d440f06`, with required CI green and all
  inline discussions resolved. The managed provider confirmed squash merge to
  `dev` at `107f1900fee0ed0c7030d3d9815d8d6df89fbdc9`. Re-read this plan and the
  parent amendment after merge; resolve only the Dispatch application-source
  publication gate. The parent effort and #8377 remain open.

### Remaining acceptance transferred to integration

The campaign/integration owner retains these explicit next stretches; source
acceptance above is not permission or evidence for any of them:

1. **Native lifetime/capture:** extend the existing capture owner
   (`agent-procutil.daemon` and its Bridge/Dispatch launch consumers), not an
   external file sweeper. Its interface must identify each stream's locations,
   timestamp coverage, child/process/runtime identity where known, completed
   versus still-open/protected capture and degraded output. Prove dual-stream
   draining, retained descendants, quiet maintenance and real Windows/POSIX
   inherited-handle rollover without rename/truncation behind writers. This
   interface is not supplied by the application handler.
2. **Installer composition:** consume #8370's owner-provided bootstrap/attempt
   discovery and completion/protection semantics; preserve active and incomplete
   attempts and primary failure results. #6812 retains presentation. No shared
   installer implementation was pre-patched here.
3. **Other source owners:** adopt elapsed-age retention and actual discovery for
   ZDD lifecycle, boot/shim/reconcile and Bridge/session diagnostics. Coordinate
   optional spool retention with the producer/path owner while preserving #8334's
   one-write append, fail-open, Unicode, format, privacy and no-parent-creation
   contract; do not rotate its raw files externally.
4. **Fleet/deployment:** explicitly own controller/connector bootstrap sinks and
   role/container output mappings through their deployment adapters and
   privacy-aware loggers. Inventory active, archived, alternate and missing/
   degraded paths; a container size/count limit is not elapsed-age retention.
5. **Qualification/activation:** qualify Linux/macOS native lanes, the real failed
   candidate/rollback/commit/retirement/serving boundary and actual adopter
   mappings. After release and explicit authorization, use the existing update
   and coordinator/supervisor lifecycle path, then verify the imported running
   version/process identity and `agent-dispatch logs` destinations. No source
   merge, release, payload refresh or HTTP health sample alone is live proof.

## Shared policy and Bridge application continuation

The reviewed convention continues beyond Dispatch: reuse its policy in the
existing dependency-free `process-telemetry` library, with explicit consumer
paths, attribution, timestamp decoder and custody. Dispatch's compatibility
adapter retains the existing public behavior; Bridge is a second actual
application consumer, not another plugin-specific copy or new framework.

Bridge's serving producer must not inherit a blocking file/handler mutex or
history rewrite. Its existing bounded diagnostic writer owns initialization,
formatting, storage and maintenance through its normal batch/idle iteration;
shutdown never waits for a stuck sink. This source work does not
ascribe an independently observed watchdog stall to logging or claim a live fix.

- [x] Reconcile the shared architecture and async/compatibility boundary before
  implementation, preserving #8370/#6812 and native lifetime ownership.
- [x] Extract shared policy without importing plugins or lock dependencies into
  the library; keep Dispatch's discovery/retention contract.
- [x] Adopt actual Bridge bootstrap, batch/idle maintenance and logger namespaces
  with bounded producer work and truthful loss/storage counters.
- [x] Expose an explicit, separate retained-spool Python API with owner-provided
  new namespace/maintenance, no parent creation and unchanged legacy factories.
- [ ] Complete native Windows source validation and normal review/required CI;
  qualify unavailable platform/live lanes separately.
- [ ] Record the accepted source merge and retain all remaining logging-owner,
  installer/native, deployment and released/running gates under #8377.

### Continuation journal

- 2026-10-24 UTC — Shared implementation retains real Bridge and Dispatch
  consumers; declared materialized consumers are Bridge, Dispatch and standalone
  Manager, each with a changefile. Existing optional-library import behavior
  remains inert and dependency-closed.
- 2026-10-24 UTC — Actual bootstrap validation identified Uvicorn's handler-close
  `dictConfig` boundary; Bridge adopts its worker after that boundary, not before
  it. Real bootstrap, quiet heartbeat, held mutex/handler lock, slow storage,
  bounded shutdown, overflow, exception-argument exclusion, concurrent retained
  spool rotation and unchanged legacy append are separate source regressions.
  No installer, raw native capture, lifecycle/bootstrap or container adapter is
  represented as delivered.
- 2026-10-24 UTC — Native Windows source portfolios pass: Bridge 115 tests,
  Dispatch 284 tests, and standalone Manager 70 tests with one optional OTLP SDK
  case skipped because that extra is not installed. Bridge exercises the actual
  Uvicorn handler-close boundary and heartbeat owner; held mutexes/handler locks,
  slow storage, overflow and close do not block its producer. Existing direct-start
  refusal coverage now explicitly pins both caller-Job observations instead of
  depending on the test runner's ambient Job; production native launch code is
  unchanged. The additional explicit millisecond-decoder regression retains
  exact-cutoff/timezone/skew and unsupported-timestamp acceptance separately
  from the shared application's ISO timestamp tests.
- 2026-10-24 UTC — Final Bridge portfolio passes 118 tests, including both
  application logger namespaces and bounded worker format expansion. Dispatch
  remains 284 passes; Manager remains 70 passes/one optional SDK skip. Ruff F/E9,
  install-contract (13 plugins), documentation consistency and diff whitespace
  checks pass. Linux/macOS native and live external qualification are not claimed
  by this explicitly source-only Windows stretch. Released helper/config
  adoption and actual running-state readback remain integration work.
- 2026-10-24 UTC — #8495 source review identified a nested mapping-key formatting
  bypass and loss of ordinary failure records with Path/exception arguments.
  Complete conversion validation now rejects unsupported mapping keys before
  interpolation; safe bounded type placeholders preserve those failures without
  producer-side repr/str/equality hooks or raw exception arguments. The actual
  registry-parse failure callsite is a regression. Required CI's shrink-only app
  size guard is addressed by factoring the existing heartbeat pause/enqueue
  boundary into the logging adapter, without widening a baseline or adding a task.
- 2026-10-24 UTC — HTTP-47 binds the application, CLI orchestrator and health
  sources as semantic owners. All three therefore remain byte-for-byte unchanged,
  with the original registry and every capture/fixture/provenance artifact.
  Register local discovery through the already-owned command registrar and use
  the existing bounded writer's batch/idle iteration for maintenance. Loss/status
  counters are ordinary `diagnostic_buffer_callbacks` records, not a wire observer,
  new status registry, timer or task. This supersedes the earlier heartbeat-hook
  candidate without weakening any captured source binding.
- 2026-10-24 UTC — Re-review's queued-maintenance starvation observation is
  covered by the final owned worker-iteration seam: maintenance is not a droppable
  queue offer and runs after normal batches as well as while idle. A regression
  fills the queue, proves the explicit maintenance offer is dropped, then verifies
  old records still expire while all 128 admitted new records are retained.
- 2026-10-24 UTC — Follow-up review identified a secondary failure if the
  fallback warning's stderr is closed/unwritable. Warning output and diagnostic
  cleanup are now best-effort, with regressions preserving both successful and
  failed primary startup outcomes. No native output handling, fixture or
  protected wire source is changed.
- 2026-10-24 UTC — The new fresh-state CLI functional smoke hit a transient
  20-second cold-import timeout; the same isolated command plus fail-open
  regressions then passed. Its child allowance is now 25 seconds inside the
  unchanged 30-second runner item cap, explicitly not a CLI-startup latency
  qualification. Controlled serving-thread contention/storage assertions and
  production timeout/watchdog settings are unchanged.
- 2026-10-24 UTC — Publication is administratively blocked, not accepted:
  the latest fail-open warning/cleanup fix is committed locally as
  `9891cdc265c38db0cf2e3a603ce60ad136ffd824` and its entrypoint/contract/diagnostic
  portfolio passes 183 tests. PR #8495 remains at
  `4a2a490c923a4f9cfacbd1c05f98823c581d7942`, with green required CI but the
  warning finding still requiring this unpublished fix/re-review. Managed
  publication first timed out and, on fresh-CWD retry, Git failed with
  `could not read Username for 'https://github.com': terminal prompts disabled`.
  Repository-scoped account resolution and API identity still agree; no ambient
  account fallback or global switch was used. No credential/config/runtime repair
  is authorized by this source-only stretch. The existing owner can inspect and,
  if authorized, repair the repository-local credential pin through
  `repos pin-credentials`, then resume normal managed push/review/CI/merge.
  Keep the worktree, source responsibility, #8377 and all broader gates open.
- 2026-10-24 UTC — Publication recovered through the existing repository-scoped
  transport without persistent configuration/credential changes or runtime
  operations. The tested fix and historical blocker journal are now published;
  the earlier blocker entry is retained as history, not current disposition.
  Fresh review identified idle maintenance retrying unavailable storage each
  worker tick. Maintenance now debounces attempts before filesystem work, while
  preserving explicit force retry and primary operation behavior.
- 2026-10-24 UTC — The new maintenance fix is local
  `21236fcf31f140ec9dcfa9cb426e40fdfc3b1b1b` (170 Bridge contract/target/diagnostic
  tests and 49 Dispatch diagnostic/telemetry tests pass). Authentication recovery
  remains valid, but two subsequent managed publications using the same scoped
  child-only transport hit the unchanged 30-second push deadline; provider
  readback confirms #8495 still has the prior published head. Read-only inspection
  finds the managed Git hook delegates through an unqualified command; a
  child-only attempt to route that command to the existing catalogue shell
  launcher fails its canonical-account-home check. These are observations, not
  a claim about the cause of the deadline or any serving-watchdog stall. No hook,
  runtime, persistent config, credential store or lock was changed; no guard was
  skipped. Current-head publication/review/CI/merge remain blocked on an
  authorized native publication carrier. Keep all source/adopter/native gates open.
- Adopter prerequisite remains **not delivered**: current telemetry configuration
  loads a zero-argument `sink` factory and registers it synchronously. It cannot
  pass the mandatory directory to `make_retained_spool_sink(directory)`, schedule
  quiet maintenance, or establish/close an owned buffer. The delivered core is
  the explicit Python API, its `maintain`/`inventory` methods and optional existing
  buffer startup/iteration hooks. An owner adapter must connect that lifecycle
  and a newly provisioned exclusive namespace before any retained configuration
  or running-retention claim; legacy factories and paths stay unchanged.
- 2026-10-24 UTC — Native managed publication recovered without persistent
  configuration, credential, hook or lock changes. Provider head readback matches
  the published maintenance fix and historical journal; current-head required CI
  passes. Fresh review identified a platform-specific test assumption: process
  identity lookup legitimately returns no start token on unsupported platforms.
  The actual consumer now checks the existing identity authority's result and a
  separate regression preserves an explicit unknown token rather than inventing
  attribution. This is a source-test correction, not macOS/live qualification.
  Native Windows managed Bridge diagnostic portfolio: 28 passed; touched-test
  Ruff F/E9 and diff whitespace checks pass.
- 2026-10-24 UTC — Subsequent current-head review identified platform timestamp
  conversion raising `OSError` for out-of-range producer values. The decoder
  boundary now treats that range failure as an unknown timestamp, preserving the
  original record while still expiring valid old records in the same segment.
  Regression coverage includes both extreme epoch values and an injected
  platform-style range error; no legacy append/factory or configuration changes.
  Native managed source validation passes 31 Bridge diagnostic tests and 49
  Dispatch diagnostic/telemetry tests; touched-code Ruff F/E9 and whitespace
  checks pass.
