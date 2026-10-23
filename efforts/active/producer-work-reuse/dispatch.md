# S2 — Dispatch declarations and health (#2110 retained slice)

[Effort](README.md) · [Validation and ownership](validation.md)

All new APIs and workload choices are agent-recommended proposals.

## Causal hotspot map

Paths are relative to `plugins/agent-dispatch/`.

- `src/agent_dispatch/supervisor_daemon.py`, `_desired`,
  `_reconcile_cycle`, `serve`: desired-state discovery repeats each cycle.
  The constructor's default five-second sleep-after-work is not the
  historical benchmark's configured thirty-second cadence.
- `supervisor_reconcile.py`, `BoundedReconciliation._declared`: the current
  discovery call/retention integration boundary; do not cache the entire
  `_desired()` result and accidentally freeze overrides or coordinator rows.
- `registrar_discovery.py:486-538`, `read_declaration_file_set`: read, decode,
  expansion, repository-local worker identity and source provenance.
- `read_location/read_repo_location_layers/discover_trusted:599-699`:
  bounded nonrecursive directory listing, pointer resolution and layered
  declarations. This is repeated parsing, not a whole-repository recursive walk.
- `RegistrarSources.refresh:912-969`: trusted and plugin-tier discovery have
  separate uncertainty retention. `scan_registrar_registry` and activation
  inputs remain part of that contract.
- `supervisor_daemon.py`, `_poll_managed_health/_poll_unmanaged_health`:
  completed futures are popped, so a healthy unit can submit another probe
  next cycle. Managed futures are already fingerprint-fenced; abandoned futures
  remain tracked until completion.
- `companion.py`, `CompanionController.health`: declared command subprocess
  through `companion_capture.py`. Probe error/invalid output is indeterminate,
  not a definitive unhealthy result.

## S2a — Cache parsed declaration generations

Own a new `registrar_document_cache.py`, focused tests, and narrow injection
points in `registrar_discovery.py`. Proposed `DeclarationReadCache.read(...)`
returns immutable decoded-document evidence, not live unit state.

Key by canonical source/installation identity, logical repository/supplier root,
authority tier, layer precedence, path and file generation. File generation
uses identity plus size and high-resolution modification/change metadata, with
read-before/read-after agreement. Retain the verified content digest of a read,
not a whole-tree hash each cycle. On filesystems where metadata cannot establish
a stable generation, conservatively reread that document; do not assert parse
reuse from a TTL alone.

Start by caching **decoding only**. Expansion still resolves its supplier,
template, worker identity and current configuration. Cache expanded declarations
only after the implementation identifies every transitive input and records a
dependency vector: pointer registry, source root mapping, layer set, activation
generation, referenced templates/config suppliers and implementation/schema
version. An unknown dependency invalidates expanded reuse. The initial parse
optimization can land without claiming expansion savings.

Keep cheap bounded directory membership reconciliation every normal cycle.
Creation/deletion/rename must become visible within the existing cycle, not a
new long cache lifetime. Preserve non-reparse admission, duplicate rejection,
trusted/plugin precedence, same-source uncertainty retention and explicit
operator overrides. A previously successful cached declaration does not turn a
currently unreadable source into complete authority. Successful removal and
unreadability remain different outcomes.

Use per-source invalidation; changing one supplier or an unrelated registration
must not clear every document or every companion's health schedule. Bound cache
entries to current tracked sources and retire obsolete entries after authoritative
membership reconciliation. No disk cache or new service is needed for S2a.

## S2b — Independently scheduled health observations

Own new `companion_health_schedule.py`, narrow daemon poll integration and tests.
Declare optional `health_interval_seconds` and bounded retry policy through the
existing `registrations.py` spec validation and `companion.py` resolution/snapshot
serialization, not through a parallel global health configuration.

**Shared seam requiring sequencing:** the declaration schema, resolution
fingerprint, `ManagedLaunchSnapshot` round-trip, and daemon health calls are
shared with runtime/recovery and telemetry work. Land schema compatibility first;
keep current default cadence when the field is absent. No broad default reduction
in health coverage is admitted by this plan.

Proposed observation key: registration ID, effective source/config fingerprint,
process incarnation (not PID alone), managed runtime generation and probe
contract. Value: tri-state result, monotonic observation time, deadline,
next-due time and in-flight holder.

- A live unchanged unit may reuse a healthy result only within its explicitly
  declared interval. It remains an aged observation, not a fresh probe.
- Every normal reconcile still polls child exit. Hard exit, changed process
  incarnation, restart, source/configuration change or takeover clears health
  evidence immediately. Fresh cached healthy cannot suppress crash revival.
- A definitive unhealthy result follows existing recovery; unavailable,
  exception, timeout and missing health capability remain `None` with reason.
  Missing probes do not create subprocesses or count as healthy samples.
- Backoff applies to repeated failed/indeterminate **probe attempts**, capped by
  a declared maximum; never back off child-exit observation or ordinary
  declaration reconciliation. Recovery success resets backoff.
- Preserve the shared per-cycle deadline and one owned future per observation
  key. An old stuck future cannot publish for a replacement and remains in the
  existing shutdown drain until it actually finishes.

## Fixed-workload acceptance

1. **Declarations:** 100 files, 100 unchanged cycles: 100 initial decodes,
   zero additional unchanged decodes, versus a structurally expected 10,000
   uncached decodes. Count actual reads/decodes/expansions independently.
   A one-file edit adds one decode; an overlay/supplier edit invalidates only
   affected expansion. Directory enumeration remains bounded and visible.
2. **Health mechanism:** six explicitly configured synthetic companions,
   100 five-second cycles over `[0,500)`, declared 30-second health interval:
   at most 17 probes per companion (102 total) instead of up to 600.
   This is a synthetic declared-policy comparison, **not** #2110's original
   installed thirty-second-lane CPU acceptance. Absent opt-in retains behavior.
   A crash at any point is detected on the next normal reconcile.
3. **Retained real qualification:** registrar plus all six normal-cadence lanes,
   unchanged thirty-second configuration, aggregate CPU <=5% of one logical
   core. Record `100 * summed process CPU seconds / wall seconds`, including
   child probe processes, process identity coverage and sample window.
4. **Separate latency gate:** actual `bridge_events.SupervisorEventWake` through
   the shared carrier, reactive lane observed enabled, durable-event
   availability to reconciliation **start** p95 <=2 seconds. Record at least
   100 independent events as a proposed minimum, individual durations,
   quantile method and clock alignment/error. Replay/duplicate events do not
   count as new samples; reconnect and durable cursor continuity must hold.
   Insufficient samples, clock uncertainty or unavailable reactive path cannot
   pass. Do not generate live events or alter production lanes without separate
   authorization.

## Validation flow

Extend `test_registrar_discovery.py`, `test_registrar_registry.py`,
`test_supervisor_daemon.py`, `test_companion.py` and `test_managed_companion.py`.
Cover atomic replacement, metadata ambiguity, inaccessible tier, activation
disable, override veto, supplier/template edits, unchanged registrations,
stuck probes, unhealthy result, missing probe, child exit during cached success
and late future after restart.

```text
python tools/run-plugin-tests.py agent-dispatch --test-file tests/test_registrar_discovery.py --test-file tests/test_registrar_registry.py --test-file tests/test_supervisor_daemon.py --test-file tests/test_companion.py --test-file tests/test_managed_companion.py
```

The implementation adds an explicit owned local executable-health fixture through
`CompanionController.health`, and a registrar consumer fixture through
`SupervisorDaemon.reconcile_once`, not only cache-unit mocks. Native CPU and
carrier latency remain separately invoked qualification lanes. Reuse the
telemetry owner's stage boundaries; do not edit `bridge_events.py` or telemetry
core to invent another timing framework.
