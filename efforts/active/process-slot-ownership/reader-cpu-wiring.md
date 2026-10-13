# CLI reader CPU observation lifecycle

Next bounded Continuation C slice of [Process Slot Ownership](README.md),
using the [landed owned CPU observations](reader-cpu-observation.md).
This decomposition is agent-recommended; no numeric CPU budget or additional
process-termination authority is introduced.

## Contract

- Agent Bridge alone opts into procutil's metrics extra. Other default
  consumers remain stdlib-only and do not start collecting automatically.
- Observe only `namespace-list` reads, not resolve, target-repo,
  ensure-ready, arbitrary user commands or all host processes.
- Capture the owned root PID and creation time at the spawn seam, before
  wait/poll/reap can make a PID-only lookup ambiguous. Use the native owned
  process handle for Windows identity, and capture POSIX identity while
  the producer still owns the unreaped child. Missing receipt evidence is
  explicit unknown, never permission to adopt a current replacement PID.
- Preserve existing output capture, command arguments, execution timeout,
  fallback, completeness, wall circuit and single-flight semantics.
  Measurement failure must not discard a valid enumeration result.
- Sample off the event loop at most once per second. Keep at most one
  pending native sampling holder per provider; if it remains unresolved
  across command completion, later readers report observation unavailable
  instead of starting overlapping measurement threads.
- Retain exact pending sampling futures in cache lifecycle accounting,
  including provider replacement and bounded shutdown. Native command exit
  and cancelled observer coroutines do not prove the sampling holder ended.
- Emit bounded observations through Agent Bridge's existing telemetry hook,
  with plugin, role, namespace key, owned PID/creation identity, wall duration,
  observed lower-bound CPU seconds, coverage, truncation and uncertainty.
  Do not emit command arguments, captured output, credentials or private paths.
- Final observation is best effort and bounded. Fast or already-exited readers
  may have no trustworthy CPU sample; retain `None`/explicit uncertainty,
  never invent zero usage or claim exhaustive child coverage.
- This slice observes and exports; cumulative role aggregation, sampler
  overhead measurement, numeric CPU budgets, admission enforcement and
  no-progress/tree reclamation remain separate parent items.
- No new resident helper, process registry, service restart, elevation or
  additional kill-on-budget behavior is introduced. Existing execution
  deadline semantics are preserved rather than widened by instrumentation.

## Validation

- [x] Native enumeration still returns the same output and fallback outcomes.
- [x] Owned spawn identity is captured before reaping; a missing/reused PID
      cannot be adopted by a delayed observer.
- [x] Native CPU activity produces a lower-bound observation through the
      configured existing telemetry sink on Windows and Linux.
- [x] Missing backend, access denial and sampling failure surface uncertainty
      without converting successful enumeration to failed/empty data.
- [x] Cancelled requests and replaced providers retain native command and
      sampling ownership without admitting duplicate work.
- [x] A stuck sampling holder cannot amplify through later readers and is
      still observed by bounded cache shutdown.
- [x] Telemetry contains neither raw command/output nor private path data.
- [x] Existing wall-admission, roster and native holder contracts remain green.

Validated in #6524: Windows and canonical Linux Agent Bridge portfolios both
passed completely. Release/adoption, role aggregation and enforcement are not
implied by this slice's completion.
