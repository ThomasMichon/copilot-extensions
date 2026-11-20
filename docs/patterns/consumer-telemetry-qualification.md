# Consumer telemetry command qualification

This source-only packet closes the bounded qualification part of #13545.
It measures the existing implementation of the
[v1 contract](consumer-telemetry-command.md), under the existing
[Portable Telemetry](../../visions/telemetry/README.md) intent. It does not
change admission, schemas, deadlines or selection. The pressure regression also
requires the existing writer to retry Windows CRT `ENOSPC` on its nonblocking
pipe within the original write deadline. Other platforms still propagate that
error. Native positive readback and deterministic deadline controls cover this
delivery correction.

## Reproduce

On native Windows or Linux, from a source worktree:

```text
python tools/run-plugin-tests.py agent-bridge --test-file tests/test_consumer_telemetry_command.py --test-file tests/test_consumer_telemetry_lifetimes.py --test-file tests/test_consumer_telemetry_performance.py --test-file tests/test_telemetry.py --test-file tests/test_process_telemetry.py --allow-explicit-tiers --timeout 180 --plugin-timeout 600 --junit-dir qualification-results/bridge
python tools/run-plugin-tests.py agent-dispatch --test-file tests/test_consumer_telemetry_command.py --test-file tests/test_telemetry.py --test-file tests/test_handoff_claim_release.py -k "(telemetry or health_only) and not wall_cpu_tail and not incompatible_reader" --allow-explicit-tiers --timeout 120 --plugin-timeout 600 --junit-dir qualification-results/dispatch
python tools/run-plugin-tests.py agent-dispatch --test-file tests/test_consumer_telemetry_command.py -k "wall_cpu_tail or incompatible_reader" --allow-explicit-tiers --timeout 120 --plugin-timeout 600 --junit-dir qualification-results/dispatch-performance
python tools/run-plugin-tests.py agent-procutil --test-file tests/test_command_pipe.py --timeout 120 --plugin-timeout 300 --junit-dir qualification-results/pipe
```

The reusable `consumer-telemetry-native.yml` workflow runs these same selections
against its exact checkout on Windows and Linux and retains JUnit artifacts.
The unrelated handoff cases that the Dispatch expression deselects are not
qualified by this packet. No installed service, plugin update, external
collector, credentials or network sink is a fixture.

## Measurement boundaries

The shared-offer packet measures each registered source separately with no sink
(legacy), disabled admission, fsync-per-record, delayed fsync-per-record,
nonreading, immediately failing and post-write fsync-failing receivers. Admission and first offer are
separate from a finite unpaced burst of 4,096 offers. This is an intentional
queue-pressure workload, not a steady-state throughput claim.
The first offer starts receiver construction asynchronously; the following
burst does not establish a warm or ready receiver. Startup and writer scheduling
can overlap it.

For each trial the property records aggregate wall time, caller-thread CPU,
whole-process CPU, first-operation time and nearest-rank p50/p95/p99/max wall
time. Per-operation timer/dispatch costs are included. Whole-process CPU
includes the writer but not the receiver process. Caller-thread CPU measures the
invoking thread: this is the emitting producer in direct-offer trials, but only
the HTTP client thread in HTTP trials, not the application/handler threads.
Whole-process CPU includes those handler threads. Windows CPU values can be
quantized, including observed zero values; zero is not proof of no CPU cost.
No baseline is subtracted. Compare modes only within
the same native run, and report absolute observations and sample count rather
than inferring overhead from a timeout or test count.

The ordinary-consumer packet separates one cold authenticated HTTP operation
from 48 further operations per mode: Bridge stops distinct already-stopped
sessions, and Dispatch creates distinct queued tasks. The property retains all
49 individual wall samples, including cold lazy initialization, and marks the
48-sample HTTP tails insufficient for tail qualification. These samples include
HTTP, database, assertion and timer costs. They do not measure terminating a live agent or an installed
daemon. Independent legacy/disabled/reading/slow/nonreading/failing cases
preserve the same ordinary operation results.

Receiver measurements, when EOF is reached, include its receive loop, delay,
write, flush and per-record fsync, plus its own process CPU. They exclude
interpreter startup and do not isolate disk service time from scheduling.
Receiver cost remains unavailable after forced retirement or early failure;
zero is not substituted. Producer setup/admission, emission, settlement and
owned shutdown are separate boundaries.

## Loss and compatibility

Readback comes from the independently opened receiver file after owned teardown,
not from a pipe-write counter. Readable bytes alone do not prove fsync completed.
Properties distinguish `readback_records` from `fsync_confirmed_records`: the
latter is known only when the receiver completed its EOF summary after successful
per-record fsync and its count matches readback. Otherwise confirmation and
offered-minus-confirmed counts remain unavailable, not zero. The injected
post-write fsync refusal proves that readable records can coexist with unknown
persistence. Records retain increasing unique sequence numbers. Properties
expose actual producer counters, readback count, sequence gaps and both available
loss counts. Terminal-route counters can overlap
previously enqueued observations; do not sum them into a fabricated loss
identity. A receiver can persist a prefix before failure. A pressure trial with
loss explicitly reports delivery as unqualified, not successful delivery.
This route has no
acknowledgment, replay or guarantee of zero loss.

Existing controls cover configuration/local-disable precedence, strict
environment, literal argv, independent admitted origins, missing origin,
receiver failure, bounded queue and custody teardown, restart source identity,
legacy factory/spool behavior and selected/running capability discovery.
The added descriptor matrix refuses unknown major versions, transport and
source schema. A real Bridge-only receiver records and fsyncs the observed
Dispatch schema before rejecting it, while ordinary authenticated task creation
still succeeds. The test checks that independent rejection witness, not merely
empty readback. This is a mixed-schema reader
fixture, not an installed historical provider. Old providers that ignore the
new admission policy still cannot enforce its disable; rollback must follow
the provider-first instructions in the producer references.

## Interpretation and release

Passing these finite controls establishes the named source behavior on the
reported native platforms. Measurements are observations, not a latency or CPU
acceptance threshold. They do not qualify sustained production load, remote
filesystems, external sinks, represented/restored Bridge export, all producer
instrumentation, every old executable, or installation/rollout.

Release remains independent: identify the promoted Main snapshot, its pinned
Dev source and successful pipeline run, verify the Bridge and Dispatch catalog/
manifest/package versions and their packaged capability files, and verify
the normal published release artifacts. A Dev merge alone is not consumable
release. Do not install a runtime merely to obtain source or release evidence.
