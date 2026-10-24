# Discoverable diagnostic logging and 30-day retention

Parent: [Linux role fleet](README.md). Integration: #8377. Standing intent:
[convention-discoverable diagnostic logs](../../../visions/machine-fleet/README.md#convention-discoverable-diagnostic-logs).
This is a reviewed-plan prerequisite, not a running-state or retention claim.

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
- [ ] Publish the bounded source through passing review and required CI.
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
