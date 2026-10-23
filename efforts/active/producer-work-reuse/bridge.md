# S1 — Bridge namespace producer reuse (#7948)

[Effort](README.md) · [Validation and ownership](validation.md)

All new APIs and thresholds below are agent-recommended proposals.

## Causal hotspot map

Paths are relative to `plugins/agent-bridge/`.

| Current symbol/file | Repeated work and correctness boundary |
|---------------------|----------------------------------------|
| `src/agent_bridge/agent_registry_cache.py:618-653`, `AgentRosterCache._loop/_refresh_cycle` | Every completed cycle sleeps the configured interval, forces discovery, then scans every namespace. Default is 12 seconds, not an exact starts/hour guarantee. |
| Same file, `_do_scan_namespace:741-760` | Invalidates the provider's short result cache before a real scan; removing this invalidation alone would relabel old data as fresh. |
| `agent_roster_discovery.py`, `_DiscoveryMixin._maybe_refresh_discovery` | Discovery success and age are separate authority. Single-flight, generation fencing and bounded caller joins already exist. |
| `agent_registry_namespace.py`, `CliNamespaceResolver._run/_list_once` | Native `namespace-list` process work, admission/backoff and optional fallback. A missing executable and an installed-but-failing provider have different meanings. |
| `agent_registry_cache.py`, `_NamespaceEntry.is_fresh/get_snapshot` | Freshness currently derives from last successful scan; stale/failed namespaces are omitted and marked incomplete. `force_refresh` forces discovery, not every already-fresh namespace. |
| `app_agent_roster.py` | Service lifecycle owns cache startup and HTTP response completeness; readers do not own producer lifetime. |

## Independent implementation slices and ownership

1. Add a narrow `namespace_refresh_policy.py` pure scheduling/evidence module
   and tests. Proposed `NamespaceObservation` contains provider identity,
   source/configuration generation, membership revision, revision-observed time,
   full-scan time, status, capability and next reconciliation deadline.
   No telemetry exporter or shared delivery API is added.
2. Integrate the policy into `agent_registry_cache.py` and
   `agent_roster_discovery.py`. Keep strong provider references, attempt
   generations, reader shielding, retained native work and bounded shutdown.
3. Add an **optional** provider-owned change-evidence capability at the
   namespace contract/`CliNamespaceResolver` seam. A revision must come from a
   real provider-owned membership source, not a hash computed by running the
   same expensive full listing on every read. A cheap already-maintained
   manifest or an existing owned event path can supply it. No new daemon.
4. Only then enable event/revision-aware scheduling for providers proven to
   implement that capability. Unsupported or unreliable providers retain the
   existing bounded full-scan behavior; report their residual cost separately.

The first implementation must identify the actual eligible provider and revision
writer before promising installed savings. A protocol field without a producer
is not a completed optimization. Recheck #5761's namespace-latency work before
touching `agent_registry_namespace.py`; it is a shared file, not permission to
take over its enumeration/admission changes.

## API and invalidation contract

- A policy decision returns `scan`, `join`, `reuse_verified_revision`, or
  `unknown`; it cannot itself declare a failed provider healthy.
- Separate discovery membership, provider membership and reader demand clocks.
  Discover provider registrations at a bounded reconciliation interval, on a
  registration generation change, and on explicit force-discovery.
- A complete same-generation membership revision can revalidate membership,
  but must retain the original full-scan age. It does **not** freshen mutable row
  fields such as activity, health or CPU. Keep those on their existing
  observation path or expose their own stale/unknown status.
- A changed revision triggers one single-flight enumeration. Demand can join
  it but cannot multiply it. Demand without a revision capability follows the
  existing stale-request contract.
- Provider identity/replacement, namespace removal, installation/source change,
  relevant configuration change, revision rollback, disconnect or a missed-event
  indication invalidate positive reuse immediately. A new source starts
  uninitialized. Late old-generation results cannot publish.
- A quiet signal alone proves nothing. A revision source must explicitly prove
  continuity/current generation. Lost notification recovery still performs a
  full scan by a documented maximum deadline. Failed discovery cannot make
  absent namespaces authoritative empty.
- Keep existing default response and `require_complete` behavior. If additive
  age/revision fields are necessary, use current protocol compatibility rules;
  an older client still gets truthful incomplete behavior.
- Separate discovery freshness from full-scan freshness deliberately; simply
  lengthening `_freshness_deadline` is not this plan.

## Fixed-workload acceptance

Use an owned synthetic provider with three namespaces, deterministic revision
publication and fixed membership/activity changes. Run 600 seconds with zero,
one and twenty readers, the same event schedule and same failure schedule.

For a candidate 300-second full reconciliation period, the revision-capable idle
case permits initialization plus scans at 300 and 600 seconds: at most three
full scans per namespace, not approximately fifty. This is a **candidate test
policy**, not an approved production default; final cadence needs measured
membership latency and reviewed loss-recovery bounds. Revision reads, discovery
work and fallbacks are counted separately so moving cost between them cannot
fake the target. For unsupported providers retain the baseline cadence/count
and report the optimization unavailable.

One membership change produces one scan per changed namespace regardless of
reader count; old results are rejected after replacement. Record event-to-visible
membership latency, full-scan and revision age, complete/incomplete responses,
process starts, child CPU, network requests/bytes and metadata reads. The
ordinary healthy GET performs no extra native scan or history replay.

## Validation flow

Extend `tests/test_agent_roster_cache.py` and `tests/test_app_agent_roster.py`
for identical-workload count assertions, force-refresh compatibility, disconnected
revision source, lost notification, source swap, cancellation-resistant old
provider, stale discovery and omitted stale rows. Retain
`tests/test_agent_registry.py` admission/failure semantics.

Focused existing runner command (from repository root):

```text
python tools/run-plugin-tests.py agent-bridge --test-file tests/test_agent_roster_cache.py --test-file tests/test_app_agent_roster.py --test-file tests/test_agent_registry.py
```

Add one explicit native consumer scenario using the real roster HTTP route and
an owned fake executable provider, not a mocked scheduler alone. Its counter
must observe actual subprocess invocations; inject one extra listing and prove
the count assertion fails. Run native cases in the explicit integration tier,
not the required unit packet. No existing agent roster is a test fixture.
