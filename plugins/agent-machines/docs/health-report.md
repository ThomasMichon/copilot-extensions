# Local telemetry reports

`agent-machines health report` is an explicit, bounded offline consumer of
existing local telemetry. It starts no collector, scheduler or service, never
changes telemetry configuration, and does not discover or publish to OneDrive.

```text
agent-machines health report --input <local-jsonl> --input <another-local-jsonl>
agent-machines health report --input <local-jsonl> --chart <new-chart.svg>
```

The command writes `agent-machines.health-report.v1` JSON to stdout. `--now`
accepts an aware ISO timestamp for a reproducible observation cutoff; the
default is current UTC time. `--chart` creates a new self-contained SVG and
refuses an existing destination, including an input file. Parent directories
are not created. Nothing opens a browser or sends network traffic.

## What is measured

- Typed `agent.performance.v1` spans use their source completion time
  (`start_time_ns + duration_ns`), component, known stage and outcome. Successful
  observed durations provide descriptive p50/p95/max per homogeneous group.
- Existing Bridge session transition/health records and Dispatch task
  lifecycle records contribute only closed-vocabulary counts. Their explicit
  `ts` is epoch milliseconds, not nanoseconds. Abandoned tasks are not silently
  called transport errors, and lifecycle records do not acquire a duration.
- Other schemas, malformed/partial records, missing timing, future timestamps
  and expired data have explicit coverage counters. Unknown raw fields,
  identifiers, routing metadata, messages and exception text are not copied.

There are 168 absolute UTC-hour buckets, including the current hour. Empty
buckets say `no-observation`; zero observed events is not a measured healthy
interval. Producer population and loss remain unknown, p99 is not qualified,
and availability is not inferred from event silence. This is neither a complete
agent inventory nor an OpenTelemetry/OTLP receiver.

The chart plots observed counts/errors and separate successful-duration p95
series for each compatible timing group. It leaves gaps and displays scan
coverage. It does not merge stage percentiles into a fleet latency metric.

## Bounds and input behavior

Each invocation accepts at most 16 explicit regular local files, 8 MiB total,
20,000 records, 64 KiB per record and 32 distinct report groups. Inputs are
processed in argument order from a size-qualified snapshot, not continuously
tailed. A repeated file identity is not counted twice. A linked/reparse,
nonregular, missing, replaced or unreadable input is reported unavailable
without exposing its path in the report. Concurrent truncation and scan limits
remain visible; no current-write or complete-population guarantee is made.

Select relevant active/archive files through their producer's existing `logs`
inventory. Do not feed native stdout/stderr, arbitrary logs, authoritative
databases or encrypted Vault artifacts to this typed consumer. Existing
producer-owned retention and custom sinks are unchanged.

Exit 0 means a report was emitted, including reports with unavailable,
unsupported or scan-limited evidence. Inspect `coverage`; it is not service
health or successful capture. Exit 2 means invalid invocation or an unavailable
output. Configuration, selected writer, actual persisted events, source release,
running adoption and synchronized peer receipt are separate facts.

## Reproducible validation

Pure tests cover time units, mixed event families, malformed/partial input,
coverage bounds, descriptive statistics and inactive chart output.
The opt-in `tests/native/test_health_report_consumer.py` exercises the existing
configured retained sink and measurement producer, independent local-file
readback, the normal CLI, chart generation and input preservation. It creates
no production service and qualifies neither a carrier nor scheduled execution.

From the source root, run the focused consumer packet with the existing managed
runner:

```text
python tools/run-plugin-tests.py agent-machines --test-file tests/test_health_report.py --test-file tests/native/test_health_report_consumer.py --allow-explicit-tiers
```
