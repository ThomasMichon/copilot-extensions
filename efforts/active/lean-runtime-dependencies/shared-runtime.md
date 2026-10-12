# Shared Runtime Reuse Investigation

[Effort and completion gate](README.md).

## Operator intent

An installer should be able to reuse compatible shared code already installed
by another service instead of reconstructing the same shared-library venv.
Efficient separate-process helpers are necessary to make that worthwhile;
shared ZDD fronts are the first proposed candidate.

## Decisions to establish before implementation

1. **Separate three forms of sharing.** Promotion-time source vendoring,
   immutable artifact/file deduplication and one reusable process/runtime are
   different mechanisms. Measure each rather than calling all of them de-vendoring.
2. **Ownership and isolation.** Define the helper owner, compatibility identity,
   marketplace/installation scope, trust policy and attributable invocation.
   Reuse must not mix caller state, credentials, routes or lifecycle authority.
   Same package name/version is not enough to select another installation.
3. **Standalone installation.** The first consumer's installer must supply all
   required capabilities. An eligible later consumer may reuse a compatible
   helper; no pre-existing sibling or mandatory global coordinator is assumed.
   Missing, stale or incompatible reuse candidates produce an explicit supported
   provisioning/refusal outcome, never arbitrary cross-venv imports.
4. **Immutability and reference lifetime.** Sharing must not mutate an active
   venv. Concurrent install/update/uninstall and rollback must preserve helpers
   still needed by another live consumer. Compatibility negotiation and
   side-by-side generations must have an owner.
5. **Efficient process contract.** Compare bounded RPC/long-lived helpers against
   repeated Python startup. Measure CPU, RSS, logical/physical storage, first
   install, second install and failure recovery, not process count alone.
6. **ZDD scope.** Determine whether sharing a front executable/runtime,
   consolidating compatible fronts in one process, or sharing immutable files
   is the useful unit. Preserve per-service endpoints, active/passive routing,
   drain, streams, authenticated control, leases and independent rollback.
   One process for every service or marketplace is not presumed correct.
7. **Reuse prior art.** Inspect ZDD, work-coalescing singleton, managed companions,
   installation cells, installer engine, artifact promotion and graceful
   cutover before inventing another lifecycle/registry subsystem.

## Required outcome

A source-cited decision table comparing artifact reuse, compatible helper
runtime reuse and shared-front process reuse; a bounded experiment with two
consumers; measured acceptance thresholds justified by baseline results; and a
reviewed compatibility/lifecycle design before any production migration.
If sharing costs more or weakens isolation, retain per-service execution and
record the evidence rather than forcing a singleton.

## Preliminary source evidence - 2026-10-12

This is investigation evidence, not an accepted production reuse design.

- `libs/zdd/pyproject.toml` declares no runtime dependencies. Its 11 Python
  source files total 169,474 bytes in the reviewed proposal snapshot.
  `libs/zdd/README.md` explicitly chooses a routing table instead of a resident
  front proxy and injects service-owned spawn, health, drain and retirement
  callbacks. There is no generic existing ZDD front daemon to consolidate.
- `libs/work-coalescing-singleton/README.md` supplies a stdlib-only warm request
  server for cheap idempotent work, with caller keys and subscriber lifetime.
  It deliberately leaves discovery, boot, leases and concurrency policy with
  the owner; it is prior art, not an interchangeable cutover broker.
- `plugins/agent-index/src/agent_index/engine/shim.py` is a real resident front:
  it activates and proxies a heavyweight embedding worker and owns idle
  shutdown. Consolidating such fronts differs from deduplicating ZDD source.

| Candidate | Likely benefit to measure | Required boundary |
|-----------|---------------------------|-------------------|
| Immutable artifacts/files | Reuse library builds and package bytes without new resident processes | Verified content/provenance, hardlink-aware accounting, no mutation of shared files |
| Compatible helper runtime, separate processes | Avoid rebuilding the same closure while retaining process isolation | Durable consumer references, version/ABI compatibility, safe update/uninstall |
| Shared multi-service front | Potentially amortize genuinely repeated warm process cost | Independent route/auth/state/lifecycle, bounded contention and failure blast radius |

The native profile experiment also demonstrates that dependency-role separation
can remove a roughly half-GiB logical host stack from the primary environment.
That is a substantially larger current opportunity than copying the small ZDD
library. It does not reject shared helpers: the next experiment must identify
the actual expensive process or build being shared, rather than introduce a
broker solely to deduplicate a cheap routing-table read.

## Bounded two-consumer process probe - 2026-10-12

The reproducible [probe](experiments/helper_process_probe.py) reads two synthetic,
distinct ZDD routing tables using the real `zdd.routing.read_table` and
`work_coalescing_singleton` TCP implementation. It compares direct warm reads,
fresh Python processes, two resident helper processes and one shared helper
process. Two concurrent trusted consumers each issue 100 requests per warm lane;
the fresh-process lane uses ten per consumer. Every reply asserts the correct
consumer identity. Helpers prove readiness with a real request and exit before
the probe returns. A closed helper must refuse requests while direct reads
remain correct.

From the repository root, using a supported Python:

```text
python efforts/active/lean-runtime-dependencies/experiments/helper_process_probe.py --rounds 100
python -m unittest discover -s efforts/active/lean-runtime-dependencies/experiments -p test_helper_process_probe.py
```

Three Windows runs on Python 3.11.9, with source imports rather than installed
package environments, produced these ranges:

| Lane | Median request latency (ms) | p95 (ms) | Warm requests per run |
|------|-----------------------------|----------|----------------------|
| Direct routing-table read | 0.293-0.372 | 0.415-0.489 | 200 |
| Fresh experiment-driver process per read | 379.631-428.284 | 393.300-493.952 | 20 cold |
| Two resident helpers | 1.505-1.805 | 18.345-24.246 | 200 |
| One shared helper | 1.925-2.390 | 24.287-25.949 | 200 |

This is an upper-level synthetic mechanism comparison, not a production CLI
benchmark: fresh drivers import the probe's libraries, and the resident lanes
read files on the same host. It does not establish installed closure size,
physical allocation, RSS, CPU savings, platform parity, authentication isolation,
concurrent installers, rollback, crash recovery or real service cutover.
The shared endpoint deliberately uses one fixture credential; consumer keys
verify result separation, not authorization. No production daemon, route,
supervisor, installation cell or active-version marker is touched.

### Evidence-led next design gate

- **Routing reads:** retain the existing direct table path. Both resident
  alternatives add overhead to an already warm client, and ZDD itself has no
  front proxy to consolidate. The cold result supports amortizing repeated
  interpreter startup when a real consumer pays it, not adding RPC to every
  routing-table lookup.
- **Compatible helper runtime, separate processes:** remains a candidate for
  avoiding repeated environment construction, not a demonstrated CPU/RSS win.
  Next measure first/second installations with content-addressed immutable
  artifacts and physical-allocation accounting before introducing a broker.
- **Shared multi-service front:** remains unaccepted. Identify two actual
  expensive fronts before measuring RSS/CPU and reviewing independent
  authentication, route ownership, drain and update failure boundaries.

For a routing-only candidate, a provisional performance screen is p95 at most
twice the existing warm direct-read p95 on the same host, before accounting for
startup/storage benefits. This generous roughly one-millisecond screen is
derived from the measured sub-half-millisecond direct baseline; both tested RPC
lanes fail it. It is not a general service-front SLA or an accepted migration
threshold. A real front's thresholds must derive from its own workload.
No reuse implementation item is closed or transferred by this partial probe.
