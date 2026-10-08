# Implementation Contract

Parent: [Session Intelligence and Accounting](README.md).

This is a proposal contract, not a description of implemented APIs.

## Evidence and identity

Retain existing session-reference/archive APIs and segment definitions. Source
observations distinguish machine, provider namespace, original session ID,
capture/generation, and content revision. Do not treat a copy or rescue capture
as fresh usage. Event identity deduplicates accounting across overlapping input
sources; conflicting payloads for the same key are observable.

Inventory includes normal machine session-state roots, nested CodeSpace
namespaces, host-merged rescues, existing compressed archives, and verified
container snapshots. Partial snapshots contribute only evidenced facts and
explicit coverage. Discovery must not collapse unavailable sources into empty
success. Provenance never creates cost or infers billing ownership.

Process logs are a separate authoritative input for provider telemetry.
Preserve them at configured targets and make compressed members incrementally
readable, including existing gzip inputs and new scheduled ZIP archives.
Compaction applies only to settled inputs under configured retention.
Quiet mtime alone does not prove a writer exited; validate active-idle processes
as well as continuous writers before adopting any legacy compaction algorithm.
Verify the replacement and source fingerprint before authorized retirement;
concurrent writers, partial copy, truncation, and rotation cannot silently lose
bytes. Do not change existing retention defaults without explicit policy.

## Derivation and catalog

Derivation is independent of prose chronicling: journaled, ongoing, and empty
sessions still need accounting records. Version every digest and statistics
contract and record content fingerprints, derivation version, timezone/rate
policy, source completeness, and revision dependencies.

Reuse existing collation and generic continuation watermark logic rather than
create a parallel meaning of a session segment. A configurable local durable
catalog supports incremental ingestion, restart recovery, immutable consumer
snapshots, additive migrations, and clean rebuild. Network filesystems are not
assumed safe for SQLite WAL. Consumer-owned workflow tables, terminal statuses,
writer-range ownership, and governed landing state remain under consumer policy.

## Accounting

Keep integer source quantities until display conversion. Provider meters,
legacy premium-request accounting, public model-list estimates, and optional
consumer-specific dollar rates are different bases. Missing meter values remain
unknown, not zero; partial sums expose metered coverage and unattributed spend.

The compatibility matrix must include:

- Event identities, timestamps, model, initiator, machine, session, tokens
  (input/cache read/cache write/output/reasoning), duration, and provider meters.
- Input composition and lifecycle usage/waste classifications.
- Elapsed intervals within continuation segments, model switches, zero first
  intervals, active-minute rate weighting, variance, and coverage.
- Subagent identity/lineage, temporal dispatch-task/review markers, task type,
  and provider/bridge provenance, preserving unresolved joins.
- Query filtering, sorting, snapshot-stable pagination, nullability, and output
  field semantics needed by existing chart consumers.

Session-state supplies metadata and attribution; process telemetry supplies
metered usage where available. Unsupported event shapes and missing sources
produce explicit diagnostics/coverage. Accounting outputs contain no prompts,
tool bodies, or raw transcript text.

## Daily aggregation and execution

Separate local derivation from an explicitly elected aggregation role. Reuse
the plugin's current scheduled-service surfaces and optional execution-provider
seams; do not introduce a resident service or port merely for library queries.
Document how role admission composes with the aggregate compiler's current
observe/enforcement state. An invalid applicable ownership plan cannot be
bypassed by a legacy execution fallback.

Derived daily storage is a sibling of the configured sessions root, not a
machine namespace pretending to be sessions. Specify a stable output layout and
schema in the first implementation slice. UTC storage and an explicit named
reporting timezone determine day boundaries; use event occurrence time rather
than ingestion time. Record timezone/rate-policy identities and source revisions.

Incremental ingestion revises affected historical days. Source/derivation
changes invalidate dependent products. Publish atomic validated outputs;
idempotent catch-up and full rebuild produce equivalent totals. Report source
coverage, late/partial inputs, staleness, and source errors. Assignment does not
authorize an automatic standby takeover.

## Consumers and release

Offer one pinned vendorable Python implementation plus machine-readable
query/export contracts, not copied forks. Document dependency closure, schema
compatibility, migrations, provenance, and container build inclusion. Existing
application routes and presentation remain adapters around shared capabilities.
Generic durable inventory/digest/accounting ownership moves upstream; private
workflow state and pricing policy do not.

Use synthetic fixtures publicly and compare existing and extracted engines
locally against a fixed corpus snapshot before consumer cutover. Record exact
integer equality and an explicit float tolerance; test empty/unpriced and
partially attributed cases, not only happy paths. No production data or private
comparison output lands in this repository.

## Agent-recommended safeguards

These are implementation recommendations, not additional operator-requested UX:

- Additive shadow migration and rollback before assigning production DB writes.
- Content-addressed source revision manifests and reproducibility checks.
- A bounded synthetic scalability test and an independently installed plugin
  smoke scenario to verify optional-sibling independence.

Each becomes a named validation obligation before code is accepted; implementation
may refine the mechanism while retaining the stated safety guarantees.
