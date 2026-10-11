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
