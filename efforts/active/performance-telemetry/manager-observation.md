# Manager source-observation slice

Parent: [Performance Telemetry](README.md). This is a proposal for the next
bounded consumer slice under #7935/#5900, not a claim of installed adoption.

## Accepted boundary

Use the existing Manager `frame_health.py` / `FrameHealthReporter` and explicit
source-native measurement/reporting seams. Do not edit the separately owned
Manager constructor, `engine_loading`, `project_config`, Worktrees, F1 or entry
files. No second bus, queue-draining writer, background service, or global
installed-producer enrollment. No existing-agent contact, agent-chat or remote
dispatch. The already authorized local receiver may accept owned validation
traffic; this slice never installs, starts, restarts or reconfigures it.

The completed producer/export task remains completed. This source episode owns
only consumer admission, the existing reporter, reporting and qualification
within these boundaries. If a reserved call site really needs a new signature,
stop and propose that exact interface to the coordinator before editing it.

## Owner-compatible design

1. **Explicit source observation scope.** A small source adapter admits bounded
   immutable configuration, trace sampling and source/payload/selected/serving
   identity once, before UI callbacks. Missing identity is recorded as unknown;
   no per-event Git, hashing, discovery or installation is permitted.
2. **One reporter owns delivery.** Reuse `FrameHealthReporter`'s existing
   128-event queue and writer. Source scope makes the admitted reporter
   available to its existing `from_env()` caller and routes early launch events
   to that same queue. The actual existing first-refresh callback still owns
   `start()`; merely admitting a scope must not fabricate a first-refresh event.
   No new writer or intermediary exporter queue is created.
3. **Worker-only SDK work.** The existing writer performs optional SDK imports,
   record-to-span conversion, local export and output persistence. Producers
   enqueue bounded content-free observations only. No callback-side network,
   file flush, process creation, SDK import or provisioning. Legacy tracing
   remains compatible; with the new mode disabled it adds no effects.
4. **Finite lifecycle and honest loss.** Preserve no-wait UI shutdown; any bounded
   drain/join belongs to the explicit source measurement owner, off the UI path.
   Slow/offline export cannot block UI or multiply writers. Include sampled-out,
   dropped, failed-export, missing terminal and incomplete-drain observations.
   Do not claim a failed callback or stopped writer delivered a complete run.
5. **Source-native adapter/report.** An explicit diagnostic driver owns a real
   executable process running the actual source Picker and reporter in isolated
   state. Its fixture roster is labeled as such; it is not production authority.
   Input is delivered through the actual Textual event path, not a fabricated
   telemetry event. The driver preserves actual process outcome and deadlines,
   and measures both enabled and disabled runs without creating per-event
   processes.

The exact source adapter shape is an implementation detail, **agent-recommended**
within this boundary: a scope/helper in `worktree_manager`, a source diagnostic
driver under `tools/performance/`, and pure report helpers in the existing
`process-telemetry` library. Do not ship an orphan framework. Packaging updates
are limited to the real Manager consumer dependency and its existing snapshot
mechanisms, with corresponding validation.

## Observation meanings

| Evidence | What it proves | What it does not prove |
|---|---|---|
| Parent's actual spawn call and child entry marker | Real executable invocation and source entry | An OS first-instruction timestamp |
| Existing `textual_first_refresh` callback | Real source Textual refresh callback | Headed compositor paint or human-visible readiness |
| Actual delivered input and observable response | Headless event delivery/acknowledgement | Physical keyboard-to-display latency |
| Fixture roster completion | The supplied source workload was rendered/observed | Current production rows or daemon authority |
| Owner-supplied authoritative revision, when available | That exact named authority observation | Authority inferred from a cached row or empty result |

Headed paint and unavailable production authority remain explicitly unavailable.
No new name may relabel a callback, fixture or file publication into stronger
evidence. Wall time is correlation; durations use an identified monotonic clock.
Cross-process clocks are not silently subtracted as if their domains were
established. Parent receipt/observation latency is distinct from child callback
latency. Total launch, renderer, exporter drain and process-exit intervals are
separate; overlapping spans are not added.

## Report contract

Each run retains:

- expected/completed sample counts, failed launches and exit statuses;
- source, payload, selected and executing/serving identity with availability;
- workload/roster/cache mode, observation method and clock domain;
- each requested boundary, including unavailable/missing observations;
- sampled/drop/export-failure/incomplete-drain counts and raw sample durations;
- p50/p95/max with the quantile method; small-N p99 is unavailable, not qualified;
- enabled/disabled overhead, outcome differences and complete-trace availability.

A partial, sampled-out or lossy run cannot pass a complete-observation budget.
Synthetic fixture outcomes remain visible in the report. The existing native
Picker/hook budgets and the broader campaign remain unchanged.

## Validation

- Unit controls prove disabled effects, once-only admission, safe typed
  projection, trace sampling, queue bounds and accurate missing/failure reports.
- Existing launch-trace regressions preserve legacy behavior.
- Controlled slow/throwing/disconnected export proves one writer, bounded loss,
  nonblocking callbacks and bounded owner cleanup.
- A real source-owned process exercises the actual Picker refresh and input
  event path. Isolation disables unrelated update/monitor/provisioning behavior
  through existing supported configuration; any remaining substitutions are
  explicitly listed rather than presented as full installed startup.
- The existing receiver independently or locally reads back exact generated
  trace/span identities and the allowlist from durable storage. HTTP success
  alone is not persistence.
- No collector/service mutation, installed enrollment, remote venue or raw
  telemetry upload is part of validation. SDK dependencies remain confined to
  the explicitly prepared isolated test environment.
- Plan review precedes code. Source tests, applicable install/snapshot and
  packaging gates, changefiles, review, CI and actual merge precede completion.

## Retained next slices

Structured logging/metric mapping, full hook composition, native installed
adoption, real headed paint, production-authoritative rows and broader process
measurements stay open under #7935/#5900. This slice must report its exact
observed boundary rather than declaring the entire campaign complete.
