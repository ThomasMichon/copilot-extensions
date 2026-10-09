# Session Intelligence and Accounting - Vision

- **Subject:** agent-logger's portable corpus, digest, accounting, and aggregation backend
- **Scope:** leaf
- **Status:** Draft
- **Last revised:** 2026-10-09
- **Reality docs:** [agent-logger architecture](../../../../plugins/agent-logger/docs/architecture.md)

## Purpose & Intent

Every captured session should become useful evidence rather than an opaque
folder that each consumer must independently interpret. agent-logger should
preserve the raw material, derive machine-ingestible session digests and
cost-attributable statistics, and maintain a durable, retrievable catalog across
machines. Any adopter should be able to build usage charts and later index or
summarize the same corpus without recreating the backend.

## Concepts & Components

- **Evidence collection:** session state and process telemetry survive collection,
  synchronization, and cold storage, with recorded origins and completeness.
- **Session derivation:** stable, versioned digests and content-free accounting
  records describe sessions, continuations, and their contributing evidence.
- **Durable catalog:** configurable, recoverable storage owns discovery and
  derivation state without absorbing a consumer's workflow or editorial policy.
- **Fleet aggregation:** an explicitly assigned machine combines the admitted
  corpus into daily accounting products; local derivation and aggregate roles
  are independently selectable.
- **Consumer surfaces:** embeddable and machine-readable backend contracts
  support independent applications, including containerized consumers.

## Features

### preserve-accounting-evidence
Session state and the process logs needed for accounting are preserved and
synchronized to configured storage. Scheduled cold compaction bounds waste
without making evidence unreadable or losing subsequent writes.

### all-session-digests-and-statistics
Every admitted session yields a machine-ingestible digest and accounting
record, including empty, ongoing, resumed, archived, remotely hosted, and
rescued sessions. Insufficient evidence produces explicit incompleteness rather
than fabricated values or invisible omission.

### chart-complete-attribution
The backend supports model token quantities, attributable cost, elapsed-minute
run rates, subagent lineage, dispatch task classifications, and bridge or venue
provenance. Evidence and coverage explain each attribution; optional sibling
metadata enriches records without becoming a prerequisite for basic accounting.

### assignable-daily-fleet-aggregation
An elected aggregation role combines sessions across machines into daily
products in the configured corpus's neighboring derived storage. Assignment,
timezone, coverage, and contributing revisions are explicit. Late evidence and
continued sessions update affected days without double-counting prior work.

### shared-backend-independent-ux
Downstream applications can vendor or embed the same catalog, digest, accounting,
and query capabilities, retaining their own UX and domain workflow. A standalone
adopter receives sufficient structured data to reproduce the supported charts.

### retrievable-cross-machine-substrate
Digests and catalog metadata preserve enough identity, origin, chronology, and
provenance for later cross-machine indexing and summarization. Accounting
exports do not need transcript contents to explain usage.

### structured-work-item-session-associations
Recorded associations between external work items and sessions are
independently discoverable without matching transcript prose. Durable metadata
preserves those associations through live, synced, and archived storage, while
query indexes can be rebuilt from that evidence. Consumers retain ownership of
their tracker, review workflow, and enrichment policy; association retrieval
does not absorb those workflows into the catalog.

## Behaviors

### identity-survives-storage-transitions
Sync copies, archive transitions, namespace changes, and repeated rescues do not
create new billable work. Original session identity, source observations, and
continuation boundaries remain distinguishable, and conflicting evidence is
reported rather than resolved by an arbitrary path winner.

### evidence-not-estimation-by-default
Provider-metered quantities, public-rate estimates, and consumer-specific
pricing are separate bases. Unknown cost is unknown, not zero. Venue provenance
cannot mint usage or infer billing ownership. Missing metadata remains visibly
unattributed.

### reproducible-rebuildable-products
The same evidence and derivation policy produce equivalent digests, ledgers,
and daily aggregates. Incremental processing, interrupted execution, backfill,
and clean rebuild converge. Derived data records its source revision and can be
recomputed without destroying consumer-owned workflow state.

### bounded-safe-collection
Processing remains bounded for large corpora and compressed evidence. Active
logs and sessions remain safe during concurrent writes; incomplete copies are
not published as complete. Compaction verifies durable replacement before any
authorized source retirement and never deletes unread accounting evidence.

### authorized-observable-roles
Collection, derivation, aggregation, and publication obey explicit applicable
configuration. Invalid ownership plans cannot authorize a valid-looking subset.
Absent sources, stale output, partial coverage, and failed runs are observable;
an unavailable elected host pauses and catches up rather than causing a second
aggregator to seize ownership.

## Non-Goals / Boundaries

- No bundled consumer dashboard, bespoke tracker, voice, or internal rate card.
- No mandatory agent-dispatch, agent-bridge, or semantic-index installation for
  local deterministic derivation; optional integrations add metadata or execution.
- No invented billing facts, silent unknown-to-zero conversion, or conflation of
  internal price estimates with provider meters or public-credit budget math.
- No forced migration of consumer workflow tables, review queues, enrichment
  policy, or rendering governance into the portable catalog.
- No automatic deletion merely because an archive or derived record exists.
- No semantic indexing or LLM summarization requirement for accounting; the
  retrievable substrate makes those independent future consumers possible.

## See Also

- Parent: [agent-logger](../README.md).
- [agent-index](../../agent-index/README.md) - independent retrieval consumer.
- [plugin services](../../../plugin-services/README.md) - portable hosting.

## Provenance

- **2026-10-09** - Folded back structured work-item/session associations and
  rebuildable lookup from durable annotation evidence, retaining independent
  consumer workflow ownership.
