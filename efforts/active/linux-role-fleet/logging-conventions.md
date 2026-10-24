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
- [x] Complete native Windows source validation and normal review/required CI;
  retain unavailable platform/live lanes as separate qualification obligations.
- [x] Record the accepted source merge and retain all remaining logging-owner,
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
- 2026-10-24 UTC — Accepted shared-source continuation: #8495 received explicit
  approval at `229c12413306e7419be43550b02630f8c02caa5a` (review
  `5637930380`), with zero open findings and all required checks passing.
  Required PR gate run `40085817128` and attribution/lockdown run `40085815747`
  qualify that exact head, not an earlier green revision. The managed provider
  confirmed squash merge to `dev` at
  `77067a7f9e8eb05deddb5e24422f99e998c484e3`, at 10:32:04 UTC.
  Re-read this plan and the parent amendment after merge. Resolve only these
  bounded source validation/publication gates; #8377 and the parent stay open.
  Latest native validation is 31 Bridge diagnostic tests and 49 Dispatch
  diagnostic/telemetry tests, plus Ruff F/E9, install-contract (13 plugins),
  immutable Bridge contracts (2 contracts, 583 fixtures) and whitespace checks.
  Earlier broader portfolios remain recorded above, not rerun or represented as
  latest-head full-suite results. Linux/macOS and live deployment remain unproved.
  Retain the clean source worktree for the integration owner; no rollout or
  finalization follows from source acceptance.

### Accepted API and next adapter boundary at #8495

At #8495 acceptance there was **no delivered retained `telemetry.json`
configuration**. Existing Bridge
and Dispatch loaders still read `"sink": "module:factory"`, call `factory()` with
zero arguments, then register it synchronously with `set_telemetry_sink`.
The legacy `"spool"` factory/path and its append behavior are unchanged. Adding a
directory beside that declaration does not make the mandatory-argument retained
factory work and does not arrange quiet maintenance or shutdown.

The accepted explicit Python interface, separately available in either plugin:

```python
from agent_bridge import telemetry as t  # alternatively: agent_dispatch

sink = t.make_retained_spool_sink(directory)
writer = t.set_buffered_telemetry_sink(
    sink,
    on_start=lambda: sink.maintain(force=True),
    on_iteration=sink.maintain,
)
locations = sink.inventory()
# At the owning process's shutdown or sink replacement boundary:
t.clear_telemetry_sink()
```

The existing owner must provision a new exclusive `directory/retained` namespace
first, without nonparticipating legacy writers. The factory never creates
parents; construction and direct maintenance are synchronous and belong off the
serving event loop. The existing owned buffer runs startup and busy/idle
maintenance, and slot clear/replacement closes it without joining a blocked
writer. `inventory()` is read-only discovery, not maintenance. The active names
are `agent-bridge-telemetry.jsonl` and `agent-dispatch-telemetry.jsonl` in that
namespace. Existing fields/`ts` remain producer-shaped; missing `ts` defaults to
epoch milliseconds. Unsupported or out-of-range timestamps are preserved as
unknown/degraded, never falsely represented as age-qualified.

**Next-phase recommendation, not implemented or accepted configuration:** add
one explicit alternative top-level `retained_spool` object with a mandatory
`directory`, mutually exclusive with legacy `sink`/`spool` configuration. Preserve
the old branch byte-for-byte in meaning and reject ambiguous mixed declarations.
The existing configuration/lifecycle owner, not a side-effecting zero-argument
factory, should resolve/provision the exclusive bucket, construct the retained
sink, call `set_buffered_telemetry_sink` with the hooks above, and expose its
inventory through the same owner's local discovery. That owner must close/clear
on shutdown and replacement. Returning a self-registered buffer from the old
factory path is incorrect: its subsequent `set_telemetry_sink` closes that owned
buffer. The adopter helper and actual configuration loaders must adopt this
boundary before configuration-only retained activation or quiet-retention proof.

Native stdout/stderr, installer attempts (#8370), lifecycle/bootstrap/session
diagnostics and Fleet/container deployment adapters remain with the named owners
in **Remaining acceptance transferred to integration** above. No retained-spool
configuration, native adapter, platform qualification or live state is supplied by
this Python API or by the application-handler merge.

## Explicit configuration adapter continuation

This bounded #8377 continuation realizes the previously recommended additive
`retained_spool: {directory: ...}` alternative through the existing configuration
loaders and owned buffer. It supersedes the historical API-only prerequisite
above only after its own source acceptance. Native/installer #8370, #6812,
captured wire owners, deployment helpers and live state remain outside this slice.

- [x] Reconcile the additive schema with the reviewed convention and existing
  buffer/retained-sink ownership; preserve legacy zero-argument factories.
- [x] Implement real configuration selection, off-producer construction and
  startup/quiet maintenance, bounded clear/replacement and local discovery.
- [x] Validate actual loader/CLI/lifetime consumers, exact age, native concurrent
  Unicode rotation, old-path isolation and fail-open behavior on Windows.
- [ ] Obtain exact-head review, required CI and provider-confirmed merge; journal
  the source contract and keep unavailable platform/live acceptance explicit.

### Adapter journal

- 2026-10-24 UTC — The existing loader selects one owned buffer with worker-only
  construction/maintenance; no self-registering zero-argument factory or new
  timer/task/collector is introduced. Native capture and all legacy paths remain
  separate. Bridge's ordinary daemon-command `finally` and Dispatch's existing
  coordinator lifespan close only their configured buffer. Captured Bridge ASGI
  source/registrar files stay untouched; embedded owners must explicitly clear
  their own slot. Local discovery reads configuration/metadata, never payloads
  or a custom factory, and explicitly leaves live health unknown.
- 2026-10-24 UTC — Native Windows managed validation passes 133 Bridge tests
  (configuration, legacy telemetry/privacy, diagnostics, buffer, unchanged #8334
  append and daemon bootstrap), 190 Dispatch tests (configuration, telemetry,
  diagnostics and coordinator), and 70 Manager source-observation tests with one
  existing optional OTLP SDK case skipped. Actual CLI discovery is read-only;
  three real processes preserve 72 Unicode records across forced small-segment
  rotation. Fresh isolated owner-provisioned namespaces exercise first selection,
  exact-cutoff startup and no-message expiry, off-loop construction/storage,
  missing namespace, blocked-worker replacement and nonjoining close.
- 2026-10-24 UTC — The selected coordinator portfolio exposed two observation
  races: GC could recover an owner already declared gone before the test observed
  its claim, and health could sample the first failed cold cycle instead of the
  healthy cycle it intended to await. Tests now declare the owner gone only after
  observing its claim and await a healthy completed cycle within the unchanged
  deadlines. No production or test timeout value changed. Full selected portfolio
  subsequently passes. Install-contract (13 plugins), immutable Bridge contracts
  (2 contracts/656 fixtures), docs consistency, module-size and whitespace guards
  pass. Captured Bridge application/CLI/health/route owners and fixtures are
  untouched. Linux/macOS native, disposable-machine installation and live external
  lanes are not exercised by this source-only Windows slice; no rollout or helper
  integration is claimed.
- 2026-10-24 UTC — #8577 review identified that a rejected retained reload could
  discard the still-active writer's cleanup reference. Both loaders now publish
  that reference only after successful selection. Actual daemon/coordinator
  regressions perform valid selection, rejected mixed-mode reload and owner
  shutdown, proving the original buffer still closes.
- 2026-10-24 UTC — Revalidation exposed a Windows sharing race in the existing
  quiet-prune observer: continuously opening the payload could deny the atomic
  replacement it was awaiting. That test now polls file-size metadata and reads
  the payload only after pruning completes. Retention, retry and all timeout
  values remain unchanged; this is not a native capture adapter or rollout.
  The seeded byte-size observer uses explicit UTF-8/LF, avoiding platform text
  newline translation. Corrected full native portfolios pass again: 133 Bridge
  and 190 Dispatch tests; touched-code Ruff F/E9 and whitespace checks pass.
- 2026-10-24 UTC — Re-review identified a test assumption of lossless admission.
  Delivery controls now retry only offers not reflected in the existing accepted
  counter, within unchanged bounded deadlines, before checking persisted records.
  A forced first-offer contention regression proves one accepted/persisted event,
  not duplicate retries. The real three-process rotation control uses the same
  admission boundary. Production drop/queue/shutdown semantics are unchanged.
  Native managed revalidation passes 134 Bridge and 51 Dispatch focused tests;
  the earlier 190-test coordinator portfolio remains recorded at its actual
  revision. Touched-test Ruff F/E9 and whitespace checks pass.
- 2026-10-24 UTC — Further re-review identified a concurrent check/clear race
  and delayed default timestamps. Configured cleanup now uses the slot's actual
  owned buffer under its existing configuration lock, without a second mutable
  plugin reference. A barrier-controlled replacement regression preserves the
  newly selected sink. Only configured buffers opt into missing-`ts` stamping
  on the bounded producer snapshot before enqueue, reserving field budget and
  preserving supplied timestamps. A blocked-construction/delayed-drain regression
  checks the original admission timestamp and unchanged input. No history/storage
  I/O moves to the producer; old factories and default buffers remain unchanged.
  Native managed validation passes 136 Bridge tests, 51 focused Dispatch tests
  and 70 Manager tests with the same one optional SDK skip. Earlier full
  coordinator validation remains recorded separately.
- 2026-10-24 UTC — The rebased contribution base exposed unrelated installer
  vendoring drift in the global install-contract guard. Supported managed sync
  retained a recovery ref and incorporated the upstream correction; the guard
  again passes for all 13 plugins. No installer implementation or vendored
  installer file was edited by this logging contributor.
- 2026-10-24 UTC — Generated timestamp fields consume the same snapshot budget
  and are added only after rejecting arbitrary keys/values; a hostile key cannot
  run comparison hooks during a missing-`ts` probe. Current managed-synced source
  passes 137 Bridge, 51 focused Dispatch and 70 Manager tests (one optional SDK
  skip), plus install-contract, immutable contracts, docs/module-size and
  whitespace guards. This is source qualification, not deployment evidence.
