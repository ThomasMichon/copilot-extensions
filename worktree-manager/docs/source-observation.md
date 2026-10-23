# Source-only Picker observations

This opt-in diagnostic consumer uses the **existing `FrameHealthReporter`
queue, writer and lifecycle**. It does not enroll installed Manager sessions,
start a collector or change any service. Normal unconfigured behavior remains
unchanged. The SDK is optional and is never provisioned from a callback.

## Actual boundary

`tools/performance/manager_native_probe.py` launches owned Python processes
running the real source `PickerApp`, its existing first-refresh callback and
actual Textual Key dispatch. It is a **headless three-row fixture**, not the
installed Manager launcher, physical keyboard/display, or production roster.
It disables advisory update/orphan polls, isolates state/plugin roots and
blocks unrelated child execution and the existing external network adapters.
Every substitution and blocked child attempt is recorded.

| Report observation | Meaning |
|---|---|
| `source_entry` | Script-entry zero-offset marker before Picker imports; not an OS first instruction |
| `first_refresh` | Script entry to the actual source Textual refresh callback; not headed paint |
| `input_ack` | Posted Key to matching application receipt, before handler/Pilot settling; not paint latency |
| `fixture_rows` | Script entry to observed headless fixture viewport; not production authority |
| `ui_stop` | Point observation that the UI reporter was asked to stop |
| `owner_drain_ns` | Off-UI diagnostic owner cleanup, measured separately |
| `process_duration_ns` | Parent-clock spawn/capture/exit interval, not subtracted from child-clock timestamps |
| `source_process_cpu_ns` | Process CPU from script entry to report, including threads but not descendants |

The parent admits source revision/dirty state and selected package version once.
Both Git reads exclude repository-selection environment overrides.
The child records its executing package version, Python/platform and source
module match. Installed payload identity is explicitly unavailable.
Root/control process invocation counts do not claim a complete descendant census.
Windows uses the existing kill-on-close Job primitive; POSIX reuses Manager's
retained group-owner helper. These are finite caller-owned processes, not services.

## Reproduction

Prepare the isolated Manager environment using the repository test runner:

```text
python tools/run-plugin-tests.py worktree-manager --prepare-only
```

Select its platform-specific interpreter under `.test-venvs`. With that
interpreter, run the diagnostic against a **fresh** output directory:

```text
<isolated-python> tools/performance/manager_native_probe.py --run-dir <new-owned-directory> --samples 3
```

For explicitly authorized export, install the source library's optional
`[otlp]` extra **only in that test environment**, preserving the seven-day
dependency policy, and provide the already-running receiver:

```text
uv pip install --python <isolated-python> --exclude-newer "7 days" -e "libs/process-telemetry[otlp]"
<isolated-python> tools/performance/manager_native_probe.py --run-dir <new-owned-directory> --samples 3 --endpoint http://127.0.0.1:<selected-port>/v1/traces --receiver-jsonl <explicit-local-receiver-jsonl>
```

Neither command discovers, installs, starts or reconfigures a receiver.
Readback checks exact generated trace/span IDs and typed component/stage/schema/
outcome labels. It returns counts, duplicate/mismatch metadata and missing IDs,
never raw receiver records. A bounded off-path wait allows receiver batching.
HTTP acknowledgement alone remains insufficient persistence evidence.

Each run produces an isolated input, sender JSONL where enabled, stderr
diagnostics and a final `report.json`. Keep raw diagnostic data local.
Mode order alternates across sample pairs; state is fresh while OS cache/load
are uncontrolled. Reports retain all samples, failed exits, missing stages,
sender/write/export/drop counts, identity availability and overhead.
Small-N p99 is unavailable, not extrapolated; p95 rank resolution is not a
statistical confidence claim.
Overhead retains individual paired differences and distinguishes their median
from the difference/ratio of condition medians. None is causal confidence on a
shared host; scheduling and uncontrolled OS cache remain part of the evidence.

**Exit zero means the requested observation flow completed, not that a latency
budget passed.** Headed/installed budget qualification is explicitly unavailable.
Native first-frame, authority, hook composition, full process-resource and
structured log/metric work remains with the
[performance campaign](../../efforts/active/performance-telemetry/README.md).

## Runtime seam

`SourceObservation` is admitted once before UI callbacks: its identity, endpoint,
sampling and clocks are read-only. `source_observation_scope(reporter)` attaches
that object to `FrameHealthReporter.from_env()` without changing the existing
Manager call-site signature. The source owner sets the existing launch-trace
flag in its own child environment so the existing callback reaches that factory.

Early source events enter the reporter's same bounded queue; the real
first-refresh callback still starts its one writer. Producers never import the
SDK, flush files, export, install or spawn children. SDK mapping/import and
bounded batches run on the writer. Unknown/raw debug, busy, argv, path and
content fields are omitted. Native stage names remain distinct from headed
`first_frame` or authoritative `current_rows`.

Source reporters are single-use. UI `close()` never waits; only the explicit
off-UI source owner calls the separate bounded `drain()`. This remains true
when the existing unmount caller supplies `close(wait=True)`.
The drain is timed independently, including failure paths. Event observation
and enqueue timestamps are separate, so delayed capture is not queue wait.
After a scope closes, late source
callbacks are dropped instead of falling back to synchronous legacy file
writes. A subsequent explicit source scope may replace the owner only after
its prior writer stops. Failed writes, incomplete drain, SDK absence, sampled
traces and export errors remain visible; diagnostics never become recovery facts.

## Targeted validation

Select `tests/test_performance_observation.py`,
`tests/test_performance_native.py`, and
`tests/production_picker/test_launch_trace.py` through the Manager test runner.
The native module uses actual source processes and the real dependency
materializer. Its Windows timeout control proves descendant cleanup.
Set `MANAGER_OBSERVATION_WINDOWS_E2E=1` only for the explicit desktop observation:
it runs two source cycles from a windowless parent and observes owned windows/
foreground transitions. Session-zero desktop evidence is unavailable, not passed.
These native controls remain outside required unit-only CI.
Readback refuses conflicting labels on any duplicate owned ID. POSIX capture
retains startup stderr through a parent-owned file while using the existing
process-group owner.
