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
