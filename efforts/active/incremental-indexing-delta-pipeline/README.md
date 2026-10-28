---
visions:
  - visions/plugins/agent-index
---

# Incremental Indexing Delta Pipeline

- **Slug:** `incremental-indexing-delta-pipeline`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs against `dev`
- **Created:** 2026-10-25
- **Status:** Active
- **Vision:** [agent-index](../../../visions/plugins/agent-index/README.md) -
  good-citizen ingestion and an engine consumers extend without forking
- **Umbrella issue:** [#9023](https://github.com/ThomasMichon/copilot-extensions/issues/9023)
- **Existing correctness owners:** #4222 (source checkpoints), #4220 (durable
  source locks), #8733 (readers observing committed writes), #5302 (vector gaps)

## Guiding Intent

Ordinary indexing rounds should cost approximately the size of the actual
change, not the size of unchanged repository or work-item history. An unchanged
round must not start embedding engines, enumerate the corpus for deletions,
rewrite stored rows, or schedule derived-index work.

The reusable change-set, checkpoint, fingerprint, embedding-reuse, store and
maintenance mechanisms belong in **agent-index**. Consumers retain their source
wire-format adapters, presentation and engine/deployment policy, then adopt the
shared mechanisms instead of maintaining a second indexing pipeline.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning author | Evidence audit, proposal and PR stewardship | #9023 and this effort |
| Shared-engine implementer | Future admitted implementation slices | Claim the relevant slice on #9023 |
| Consumer maintainers | Source-adapter integration and consumption proof | Their own repositories and trackers |

## Coordination

- **Topology:** independent per-slice PRs, ordered by real interface dependencies.
- **Host (owns proposal PR):** Planning author.
- **Delegates:** read-only engine, intake and storage evidence tracks; no delegate
  was authorized to edit runtime code or operate a service.
- **Handoff:** findings integrate into [audit.md](audit.md); future implementers
  claim a named slice before editing.
- **Admission:** implementation was admitted after the proposal merged.
  Consumer rollout retains its separate confirmation gate. A merged proposal
  is not evidence that any optimization has shipped.

## Context

The current pipeline already has useful building blocks: changed-content
discovery, a crawl manifest, batch checkpoints, retry-time fingerprint checks,
queued-task coalescing, incremental FTS maintenance and durable worker recovery.
They do not yet form an end-to-end, delta-bounded contract.

The [source-backed audit](audit.md) separates confirmed work amplification from
unmeasured physical storage costs. The [validation matrix](validation.md)
defines operation counts and safety cases, not a timing-only benchmark.

This is vision-closing: the existing vision requires incremental, event-led,
rate-aware ingestion and a shared engine. Provider-specific parsing fixes stay
with their adapter owner. Existing locking, checkpoint, reader-currentness and
coverage issues retain their own ownership; this effort coordinates with them.

## Request

Operator request, with the downstream product identifier redacted for public
publication; no behavioral constraint was removed:

> File tracked issues, then make a plan, and identify more inefficiences
> blocking pure incremental flows with minimal scanning per round. Ensure that
> agent-index receives the bulk of the fixes with [downstream consumer] being
> a beneficiary

The phased mechanisms and quantitative acceptance criteria below are
**agent-recommended implementation proposals**, not claims that the operator
specified each API or authorized execution.

After the reviewed proposal, the operator admitted execution:

> Drive the work

## Plan

### Phase 0 - Audit and reviewed proposal

- [x] Establish #9023 and inspect current source, preserving existing owners.
- [x] Audit independent engine, intake and storage/maintenance tracks.
- [x] Author the canonical proposal with explicit consumer ownership and
  operation-count acceptance criteria.
- [x] Obtain implementation admission ("Drive the work", 2026-10-25).

### Phase 1 - Shared delta and compatibility contracts

- [ ] Specify an additive, versioned change-set: canonical source/account scope,
  base/target revision or cursor, changed identities, explicit tombstones,
  provider metadata revisions and snapshot-completeness evidence.
- [x] Distinguish event deltas, bounded polling and complete reconciliation.
  Incomplete/truncated pages must never authorize deletion of unseen items.
- [x] Specify lossless queued unions and trailing deltas for changes received
  during active work. Preserve existing full-task sequencing guarantees.
- [x] Design external-provider capability/version negotiation and staged
  producer/consumer compatibility using the existing contract-evolution policy.
  Legacy providers remain usable through an explicitly observable reconcile
  path; never silently drop a required delta field.
- [ ] Establish per-stage counters and tiny deterministic fixtures before
  changing runtime behavior.

### Phase 2 - Shared no-op, fingerprint and embedding reuse

- [x] Reuse source metadata/revisions before fetching content where supported.
  Add normal-round fingerprint checks, not only retry-window skipping.
- [x] Separate text, searchable metadata, chunker/schema and embedding-profile
  identity. Metadata-only updates must not invalidate unchanged vectors.
- [x] Make embedding selection operate on missing/invalid chunk-model pairs,
  with bounded batch lookups and resumable coverage/backfill.
- [x] Delay expensive model readiness until a nonempty embedding plan exists.
  Keep search availability independent of indexing-engine lifecycle.
- [ ] Preserve store-before-checkpoint ordering, partial-write recovery,
  source isolation and compatibility with #4222/#4220.

### Phase 3 - Delta-bounded discovery and reconciliation

- [ ] Carry exact changed IDs/ref ranges through intake, queue and connector
  boundaries; merge duplicate deliveries without erasing distinct changes.
- [x] Make local Git use commit-range changes and tombstones without enumerating
  all tracked paths and commit history on each ordinary round.
- [ ] Bound hosted issue/PR acquisition and timestamp overlap replay by cursor
  and identity. Provider limitations must remain explicit.
- [ ] Persist acquisition progress so retry/restart does not restart a
  corpus-wide listing.
- [ ] Move authoritative full enumeration into separately budgeted, resumable
  reconciliation slices. A missing checkpoint or expired cursor requests
  visible repair; it must not disguise an unlimited rebuild as a tiny update.

### Phase 4 - Delta writes and derived maintenance

- [x] Skip identical content/vector upserts; support targeted metadata updates
  and batched stale/deleted chunk cleanup.
- [x] Publish a logical mutation result/generation once per committed delta.
  Only actual projection changes mark FTS or cluster slices dirty.
- [x] Preserve the existing incremental FTS path; coalesce work and distinguish
  ordinary updates from first-build/recovery rebuilds.
- [x] Scope cluster invalidation by affected model/bucket where safe. Keep
  inherently global repair/compaction explicit and amortized rather than
  claiming every maintenance algorithm can be strictly local.
- [ ] Coordinate store-reader freshness with #8733 and missing-vector repair
  with #5302; do not invent parallel authorities.
- [x] Connect explicitly budgeted point/file-index preparation and bounded
  upkeep/consolidation to ordinary ingestion. Equal API counts and a prepared
  fixture do not qualify an unprepared store or an ever-growing unindexed tail.

### Phase 5 - Consumer integration and qualification

- [x] Reuse the existing multi-model engine and qualify its supported connector,
  profile, embedding-client and progress hooks; add only missing injection or
  lifecycle seams rather than creating a second multi-model engine.
- [x] Demonstrate a generic single-model consumer and an injected multi-model
  consumer use the same engine path without importing private integrations.
- [ ] Publish a consumer migration recipe and staged compatibility tests.
  Consumers re-sync the shared engine and remove duplicated orchestration;
  adapter-only patches are not a substitute for shared fixes.
- [ ] Qualify release, actual consumed source and a separately authorized
  rollout. Keep merged, released, consumed and serving evidence distinct.

## Validation Plan

- [ ] Pass the [count-based matrix](validation.md) at 100, 10,000 and 100,000
  unchanged synthetic items without ordinary work growing with corpus size.
- [ ] Preserve metadata freshness, tombstones/renames, model backfill, complete
  checkpoint advancement, interrupted-run recovery and source/account isolation.
- [x] Demonstrate duplicate and active-round event bursts are losslessly
  coalesced with bounded unique-item fetches.
- [x] Prove no-op rounds produce zero embedding requests, content/vector writes
  and derived-maintenance runs.
- [ ] Exercise both built-in and external-provider compatibility, including
  old producers/consumers and incomplete-page refusal.
- [ ] Retain the existing checkpoint, source-lock, incremental-FTS and
  processing-full/trailing-incremental tests. Add small contract tests to
  required CI; keep scale/native composition in existing bounded opt-in lanes.
- [ ] Require separate consumer evidence: shared-engine calls, exact vendored
  provenance, stable retrieval results and measured stage counts on real deltas.

## Proposal

The smallest useful sequence is shared contracts and counters, normal-round
reuse, bounded acquisition, then store/maintenance optimization. Adapter wire
repairs can be staged independently but must not become a second engine.
Implementation should use dependency-ordered, reviewable PRs, with explicit
source-owner coordination rather than one large rewrite.

## Journal

Append-only entries are preserved in dated files, newest first.

- [2026-10-27 provider measurements](journal/2026/10.27%20provider-work-measurements.md)
- [2026-10-26 storage](journal/2026/10.26%20incremental-indexing-storage.md)
- [2026-10-26](journal/2026/10.26%20incremental-indexing.md)
- [2026-10-25](journal/2026/10.25%20incremental-indexing.md)
