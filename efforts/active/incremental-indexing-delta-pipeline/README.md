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
- [ ] Distinguish event deltas, bounded polling and complete reconciliation.
  Incomplete/truncated pages must never authorize deletion of unseen items.
- [x] Specify lossless queued unions and trailing deltas for changes received
  during active work. Preserve existing full-task sequencing guarantees.
- [ ] Design external-provider capability/version negotiation and staged
  producer/consumer compatibility using the existing contract-evolution policy.
  Legacy providers remain usable through an explicitly observable reconcile
  path; never silently drop a required delta field.
- [ ] Establish per-stage counters and tiny deterministic fixtures before
  changing runtime behavior.

### Phase 2 - Shared no-op, fingerprint and embedding reuse

- [ ] Reuse source metadata/revisions before fetching content where supported.
  Add normal-round fingerprint checks, not only retry-window skipping.
- [ ] Separate text, searchable metadata, chunker/schema and embedding-profile
  identity. Metadata-only updates must not invalidate unchanged vectors.
- [ ] Make embedding selection operate on missing/invalid chunk-model pairs,
  with bounded batch lookups and resumable coverage/backfill.
- [x] Delay expensive model readiness until a nonempty embedding plan exists.
  Keep search availability independent of indexing-engine lifecycle.
- [ ] Preserve store-before-checkpoint ordering, partial-write recovery,
  source isolation and compatibility with #4222/#4220.

### Phase 3 - Delta-bounded discovery and reconciliation

- [ ] Carry exact changed IDs/ref ranges through intake, queue and connector
  boundaries; merge duplicate deliveries without erasing distinct changes.
- [ ] Make local Git use commit-range changes and tombstones without enumerating
  all tracked paths and commit history on each ordinary round.
- [ ] Bound hosted issue/PR acquisition and timestamp overlap replay by cursor
  and identity. Provider limitations must remain explicit.
- [ ] Persist acquisition progress so retry/restart does not restart a
  corpus-wide listing.
- [ ] Move authoritative full enumeration into separately budgeted, resumable
  reconciliation slices. A missing checkpoint or expired cursor requests
  visible repair; it must not disguise an unlimited rebuild as a tiny update.

### Phase 4 - Delta writes and derived maintenance

- [ ] Skip identical content/vector upserts; support targeted metadata updates
  and batched stale/deleted chunk cleanup.
- [ ] Publish a logical mutation result/generation once per committed delta.
  Only actual projection changes mark FTS or cluster slices dirty.
- [ ] Preserve the existing incremental FTS path; coalesce work and distinguish
  ordinary updates from first-build/recovery rebuilds.
- [ ] Scope cluster invalidation by affected model/bucket where safe. Keep
  inherently global repair/compaction explicit and amortized rather than
  claiming every maintenance algorithm can be strictly local.
- [ ] Coordinate store-reader freshness with #8733 and missing-vector repair
  with #5302; do not invent parallel authorities.

### Phase 5 - Consumer integration and qualification

- [ ] Reuse the existing multi-model engine and qualify its supported connector,
  profile, embedding-client and progress hooks; add only missing injection or
  lifecycle seams rather than creating a second multi-model engine.
- [ ] Demonstrate a generic single-model consumer and an injected multi-model
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
- [ ] Demonstrate duplicate and active-round event bursts are losslessly
  coalesced with bounded unique-item fetches.
- [ ] Prove no-op rounds produce zero embedding requests, content/vector writes
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

### 2026-10-25 - Planning slice

- Created #9023; verified #4222 and #4220 remain open correctness owners.
- Audited source snapshot `a6d1d31e549f899026ad159d4ec31e6ed2d820bd`.
- Recorded current mechanisms, avoidable work and proposed operation-count
  gates. No indexing implementation, service operation or deployment is part
  of this proposal.
- Independently ran the existing no-resume re-embedding test (1 passed) and the
  queued-full sequencing/incremental-FTS suites (22 passed), using bounded test
  supervision. These prove current behavior and retained protections, not
  completion of the proposed optimizations.

### 2026-10-25 - Execution admitted

- The operator admitted execution after proposal #9024 merged. Claimed the
  shared implementation slices on #9023; existing correctness owners and
  separate consumer rollout gates remain unchanged.
- Begin with a versioned complete-delta seam and normal-round no-op reuse,
  using operation-count regressions before expanding acquisition and storage.
- Implemented the first opt-in Python delta seam, bounded file-checkpoint
  lookups, conservative normal-round reuse and lazy engine readiness.
  Targeted delta/reuse/source-family/worker/lifecycle tests pass (56 passed,
  1 existing skip), including 100/10,000/100,000-row fixtures. Task/event
  payloads, external-provider negotiation, per-chunk/model reuse and
  maintenance generations remain open; this is not end-to-end completion.

### 2026-10-25 - Durable hint receiver merged

- Merged #9046 as `507bd27ff` after current-head Copilot approval, green CI
  and an independent 63-test queue/domain/sequencing/worker selection.
- Version-1 acquisition hints now preserve exact source/family/account scope,
  changed identities, distinct ref ranges, event IDs and metadata revisions.
  Queued unions are lossless and processing work retains a separate trailing
  row. SQLite fences keep hinted rows invisible to legacy dequeue code,
  including after an older retry/release operation.
- This is receiver-first expansion only. Worker/HTTP/CLI capability migration
  and connector consumption remain the next intake stretch; the queue merge
  does not enable hinted execution or complete Phase 1.

### 2026-10-26 - Capability-aware hint transport merged

- Merged #9049 as `7842464c3` after current-head Copilot approval and green CI.
  Independently qualified the final worker-settlement, Python-floor,
  intake-settlement, execution and governance selection (42 passed);
  the implementation packet passed 184 tests with one Windows-only skip.
- HTTP, CLI, foreground and detached-worker paths preserve required version-1
  hints. The actual selected callable, not package metadata, declares execution
  support. A refusing worker generation parks hinted tasks while ordinary
  tasks continue; changing generation permits revalidation, not blind execution.
- Cancellation-safe queue admission/settlement retains drain custody until
  outstanding writes settle. The Python 3.10 import surface remains supported.
- The real engine still does not advertise hint execution. Connector semantics,
  full external-provider negotiation and end-to-end event qualification remain
  open. The next discovery slice is pinned Git ranges and tombstones; the core
  recovery/reuse PR remains under review.

### 2026-10-26 - Replay-safe delta core merged

- Merged #9043 as `f63927fcb` after current-head Copilot approval and green CI.
  The final credential/recovery/HTTP packet passed 86 tests and the Azure
  DevOps suite passed nine. Earlier bounded recovery/GC/source/lifecycle
  selections passed 131 tests with one existing skip; operation-count fixtures
  cover 100, 10,000 and 100,000 unchanged path-index entries.
- Complete in-process deltas, exact bounded file-checkpoint lookups,
  conservative normal-round reuse and lazy embedding readiness are available.
  Invalidation precedes writes; stale cleanup and tombstones precede source
  cursor publication. Acquired manifests survive partial application and replay
  before newer upstream changes.
- Recovery binds effective connector targets/accounts, rejects overlapping
  pending families atomically and defers retirement while acquisition remains
  pending. Installation credentials remain usable without a user endpoint;
  their opaque credential scope conservatively refuses rotation of pending work.
- This closes the lazy-readiness Plan item only. Acquisition-page checkpoints,
  account-wide source leases/shared alias watermarks, per-chunk/model reuse,
  mutation-aware maintenance and consumer qualification remain open under
  their existing owners. The next implementation slice is pinned Git discovery.
