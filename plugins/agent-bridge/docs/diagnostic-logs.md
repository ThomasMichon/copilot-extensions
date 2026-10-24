# Application diagnostics and local discovery

`agent-bridge logs` returns read-only JSON discovery from the existing
`effective_config_dir()` and `install_dir()` owners. It creates no directories,
contacts no daemon, starts no worker and returns no log contents. Active and
UTC/unique archived application files live under
`<effective-config-root>/logs/diagnostics/daemon.jsonl`. Elevated/alternate config
roots stay distinct. Missing files and unknown write health are explicit.

The inventory also exposes known native stdout/stderr, lifecycle, boot-trace
(including its explicit override), reconcile and fallback locations, labeled
**not managed by this application handler**. Session/event databases and
ownership receipts are authoritative state, not log-expiry targets.

## Actual owner and compatibility

The explicit daemon `start` entrypoint adopts both existing Bridge logger
namespaces (`agent-bridge.*` and `agent_bridge.*`) after `uvicorn.Config` performs
its actual `dictConfig` boundary, which closes existing handlers. Installing the
writer before that boundary would leave a closed queue on a serving daemon.
Ordinary CLI invocations do not configure application files.

The entrypoint owns closure through its existing `try/finally`, including startup
failure. Logging initialization errors produce a bounded stderr warning without
replacing the primary startup result. No service/routing/activation/termination
authority changes. A closed writer still blocked in its sink is never replaced
with another owned worker.

Owned application loggers stop duplicating their records through the synchronous
root stream handler. Consumers of the former raw/text destinations must select
the new application files through `logs`. Native launch handles and their paths
are neither renamed nor truncated; stdout/stderr, third-party/root logging and
session-child diagnostics are not adopted by this change. No transparent text/
JSON compatibility or complete native capture coverage is claimed.

## Async producer and storage policy

The actual application adapter reuses the optional shared
`process_telemetry.diagnostic_logs` policy used by Dispatch, through the existing
bounded `BufferedSink`. There is no second collector, daemon or logging registry.

Producer work is bounded primitive copying and a nonblocking queue offer:
no handler mutex wait, message formatting, filesystem operation or history scan
runs on the asyncio serving thread. Nonprimitive arguments (including Path and
exception objects used by existing failure callsites) become explicit bounded
type placeholders, never repr/str/equality calls; long primitive arguments are
clipped to the copier's string limit. `argument_adaptation` records that context
loss without dropping the whole failure. Unsupported/oversized envelopes,
contention, overflow, callback failure and abandoned work are observable losses. The existing
queue holds at most 128 records plus one 32-record drain batch, with the copier's
primitive/string limits and 64 KiB per-record serialization estimate. These are
not heap-size or lossless-delivery guarantees. Worker formatting rejects dynamic
or over-512 field width/precision and over-8192-character results as failed
callbacks, rather than allowing a small format string to allocate unbounded output.
The validator parses every complete conversion and rejects nested-parenthesis
mapping keys before interpolation, rather than relying on a partial regex match.

The worker formats approved primitive arguments and writes UTC `ts`, service,
component, logger, level, PID, existing OS process-start token (null if unknown),
imported runtime version and actual interpreter prefix. Only explicit
operation/correlation/generation extras are copied. Exception diagnostics contain
the type and up to eight frame locations, without raw exception arguments,
exception objects or source-line reads. This is not a replacement for a full
native traceback or a generic redactor; caller-owned privacy adapters remain
authoritative.

Startup maintenance runs on that same worker before its first batch. Normal
maintenance uses its existing per-batch/idle iteration and existing stop wait:
no separate timer/task/thread and no change to the protected application heartbeat.
The worker debounces maintenance and applies the same exact
UTC 30-day record-age policy, cutoff equality, future/skew handling, interrupted
tail isolation, archive pruning and protected unknown timestamps as Dispatch.
Both active and archived application records are maintained while quiet.
The debounce includes failed attempts, so unavailable storage is not retried on
every idle iteration. Explicit owner force retry remains available off-path.

The consumer injects its existing operation lock. Its two-second retry deadline
is **worker-only**, not a serving-thread lock wait or a hard deadline on kernel
filesystem I/O. A slow/stuck storage call can occupy at most the existing worker;
producers continue with bounded loss. Closing rejects new work and abandons
queued diagnostics without join, flush or retained-descendant EOF waits.

On eligible maintenance, the same worker writes an ordinary application JSONL
record containing `diagnostic_buffer_callbacks` and the sanitized storage error
class. This keeps loss observations discoverable in the existing log output,
without a status registry, new endpoint or any change to the protected health
wire response. Counters include maintenance callbacks; successful callback
returns do not prove durable persisted records. The in-process owner also has
`logging_status()`/handler statistics. Read-only `logs` discovery cannot prove
another process's write health. None of these observations establishes serving
generation, activation success, process authority or rollout permission.

An independently observed serving-watchdog stall remains a separate unresolved
boundary. These source tests prove the bounded logging path under controlled
contention/storage/history/shutdown cases; they neither identify that stall's
cause nor constitute a live fix.

## Deliberate retained-spool adoption

The new Python-only `telemetry.make_retained_spool_sink(directory)` is an explicit
opt-in for an owner-provisioned **new** `directory/retained/` namespace. Existing
`make_spool_sink`, configured spool paths and the one-write append contract are
unchanged and unretained. The owner must exclude old/nonparticipating writers,
maintain quiet sinks and expose `sink.inventory()`. Missing parents are not
created. Producer fields and existing epoch-millisecond timestamps remain intact.
See the [shared API contract](../../../libs/process-telemetry/README.md).

## Remaining owners and activation

#8377 remains open. `service_process_cli._launch_bridge_daemon` still passes
`agent-bridge.log` and `agent-bridge-err.log` into `agent-procutil` native capture.
That lifetime owner must expose per-stream locations, timestamp coverage,
process/runtime identity, completion/protection and degraded capture before safe
age retention can be claimed. Prove dual-stream draining, inherited handles and
retained descendants; an application queue is not that adapter.

#8370 retains bootstrap/installer-attempt discovery and protected active/incomplete
attempt semantics; #6812 retains progress formatting. No interface implementation
from those owners is supplied or pre-patched here. Lifecycle/bootstrap, other
native/session writers and Fleet/container deployment sinks also remain open.

After release, only an explicitly authorized existing update and service
lifecycle flow may activate this source. Verify the actual imported process,
health counters and log readback. Source merge, a payload refresh or an HTTP
health response alone is not running-state qualification. Linux/macOS and live
external lanes remain separate where not exercised.
