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
- [ ] Make embedding selection operate on missing/invalid chunk-model pairs,
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
- [ ] Publish a logical mutation result/generation once per committed delta.
  Only actual projection changes mark FTS or cluster slices dirty.
- [x] Preserve the existing incremental FTS path; coalesce work and distinguish
  ordinary updates from first-build/recovery rebuilds.
- [ ] Scope cluster invalidation by affected model/bucket where safe. Keep
  inherently global repair/compaction explicit and amortized rather than
  claiming every maintenance algorithm can be strictly local.
- [ ] Coordinate store-reader freshness with #8733 and missing-vector repair
  with #5302; do not invent parallel authorities.

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

### 2026-10-26 - Pinned Git discovery merged

- Merged #9058 as `9dc29b068` after current-head Copilot approval and green CI.
  The final bounded Git/recovery packet passed 125 tests; both new parsing
  helpers pass mypy. The initial integrated post-rebase packet passed 140 tests.
- Revision-backed Git discovery now uses immutable object reads, raw range
  diffs, explicit tombstones and new-commit history only. A no-op performs zero
  range/blob/history commands after resolution; one file plus its commit uses
  five, identically with 100, 10,000 and 100,000 unchanged stored items.
- Full reconciliation remains explicit for missing or rewritten cursors.
  Working-tree fallback retains its logged legacy reconciliation behavior.
  Unsupported non-UTF-8 paths are logged consistently, commit-message control
  characters retain their content, and portable rename coverage stays active
  on Windows.
- This closes supported revision-before-content reuse and the revision-backed
  Git discovery Plan items. Hosted acquisition, actual hint-consuming
  connectors, external-provider qualification, page-level recovery and derived
  maintenance remain open. Continue with negotiated provider integration and
  no-op derived-artifact coverage reuse; no consumer rollout is implied.

### 2026-10-26 - Covered derived-maintenance implementation slice

- Added conservative physical-version coverage for cluster artifacts and actual
  native FTS coverage checks. Covered ordinary rounds skip item enumeration,
  cluster publication and FTS-builder subprocesses. Full repair remains explicit.
- Cluster rows and coverage publish atomically; computation-time mutation,
  failed passes, policy/model changes and same-name/version table reincarnation
  cannot validate stale coverage. Existing source-domain authority is retained.
- Review identified surviving older workers as a separate artifact writer.
  Database triggers now revoke coverage on row mutations even when the writer
  predates the coverage schema. The exact old empty-replacement SQL and all six
  artifact insert/update/delete boundaries pass regression tests (39-test
  focused maintenance/API packet).
- A second review found tolerant legacy reads could mask transient failures
  as empty inputs. Cluster construction now selects complete, strict reads;
  native one-time item/chunk/vector failures preserve prior artifacts and leave
  the next unchanged round due for recomputation (98-test retained packet).
- Native fixtures with 100, 10,000 and 100,000 content rows in both layouts
  measured the same metadata-only work for two covered rounds: 12 legacy or 24
  source-aware table-refresh calls, zero enumeration, builder, index recreation
  or artifact replacement, with unchanged index metadata and artifact timestamp.
  These fixtures seed a known-empty cluster artifact because they have no
  vectors; they do not claim a measured 100,000-item full clustering pass.
- Targeted maintenance/lifecycle/FTS tests passed (64, one Windows-only skip);
  retained source-store and multi-model tests passed (55, eight Windows-only
  skips). Source-aware child-failure coverage now writes actual uncovered data
  before injecting failure rather than requesting redundant covered work.
- This advances Phase 4 but does not close its logical-mutation or local-slice
  gates, the complete count matrix, consumer integration, release or rollout.
  Continue with negotiated provider checkpoint fencing, precise chunk/model
  reuse and the shared consumer injection path.

### 2026-10-26 - Negotiated provider and completed-checkpoint isolation merged

- PR #9059 merged as `6a48ea7efd3fbbbe811b8eb261d364fb6d737fed`
  after current-head Copilot approval and required green CI. Independent
  provider/checkpoint/recovery/Git/compatibility tests passed (278).
- Provider metadata advertises additive v1 support; only supported receivers
  receive the delta flag. Negotiated full, changed-entry and path-list responses
  echo executing family/account/target before data can affect stored rows.
  Old/future receivers retain the observable legacy reconciliation path.
- Completed checkpoints now persist the existing acquisition-scope identity,
  not only pending journals. Missing or changed scope requires explicit full
  reconciliation before cursor reuse; successful transitions bind identity and
  cursor together, and failures retain prior state and recovery evidence.
- The external negotiation design gate is resolved. Actual hint consumption,
  hosted pagination/recovery, full source-lease ownership, shared embedding
  reuse and downstream adoption remain open. Continue those planned slices;
  this merge is not an effort completion or deployment.

### 2026-10-26 - Covered-maintenance slice merged; shared reuse continues

- PR #9060 merged as `ccd939ebd46005d06f2774bc35cd5bcf09eba76d`
  after current-head Copilot approval and required green CI. This includes
  native covered-round counts, mixed-version artifact invalidation and strict
  failure-propagating clustering reads.
- The merged provider/checkpoint/strict-maintenance composition passed 414
  focused tests with one Windows-only skip. This verifies the two independently
  reviewed source slices together, not just their isolated pre-merge branches.
- The existing incremental FTS/coalescing/full-repair gate is resolved. The
  logical mutation generation, identical-write avoidance and model/bucket-local
  maintenance gates remain open; physical-version coverage does not substitute
  for them.
- Continue the shared chunk/model reuse slice and supported consumer injection
  seams, then qualify downstream adoption and the remaining count matrix.
  Release, consumption and a separately authorized rollout are still required.

### 2026-10-26 - Chunk/model reuse implementation slice

- Presented files that miss their whole-file fingerprint now select only
  missing or invalid routed chunk/model pairs. Derived SQLite receipts bind
  raw embedding input/model policy to the actual stored float32 vector digest;
  bounded strict presence checks reject missing or legacy-overwritten vectors.
- Invalidate before potentially partial vector writes and publish only after
  verifying the completed batch. Restart, failed-cleanup replay and profile
  A/B/A rollback preserve conservative recovery; full repair forces writes.
- Metadata-only updates retain vectors and skip readiness when coverage
  matches. Source-aware provenance synchronization avoids even a native table
  update when source/language already match. Changed files still refresh
  canonical content for timestamp cleanup; content no-op writes, logical
  mutation generations and global gap repair remain their own open gates.
- Pair/routing/checkpoint/recovery/native source-store regressions passed
  (130). Selected-receipt fixtures at 100, 10,000 and 100,000 unrelated rows
  retain fewer than 100 SQLite VM steps and one selected vector lookup.
  Native metadata-only tests preserve vector table versions in both layouts;
  routing, bounded large-batch reads and interrupted-cleanup retry also pass.
- This implements another bounded Phase 2 slice, not the full count matrix,
  consumer adoption, release or authorized rollout.

### 2026-10-26 - Chunk/model reuse reviewed and merged

- PR #9065 merged as `79713efa87b969cf62be0e1fc3f4f6f49f3d31f8`
  after current-head Copilot approval and required green CI. Reconciled the
  existing implementation worktree; no continuation worktree was created.
- The text/metadata/chunker/profile separation gate is resolved by raw-input
  pair qualification and native metadata-only vector preservation. Selection
  and restart backfill for presented files are implemented, but discovering
  unchanged files requiring a newly added profile still needs an explicit,
  bounded coverage/backfill pass. The complete backfill gate remains open.
- Continue with the minimal shared consumer configuration/source-selection,
  client/lifecycle and worker-callable injection seams. Reuse the existing
  connector registration API, and preserve actual-callable hint negotiation.
  Exact hinted acquisition, bounded hosted pagination, content-write avoidance,
  logical mutation maintenance and the full validation matrix remain open.

### 2026-10-26 - Shared consumer injection slice

- Added optional explicit config/source selection and one typed embedding
  client/lifecycle bundle to the existing engine. Existing connector
  registration remains the source extension API; default callers retain
  late-bound defaults and the same pipeline.
- Added optional worker config/callable injection without a process-global
  engine replacement. Explicit config is forwarded to the default engine;
  custom adapters bind their own config. Actual-callable hint capability,
  resume, progress, cancellation, partial status and result accounting remain.
- Native single- and dual-model consumer fixtures exercise the shared path,
  metadata-only vector reuse, registered-source full GC and restart cursors.
  Factory/close/source failure controls retain owned cleanup and stop only
  engines started by this run. The expanded bounded packet passed 150 tests.
- The first two Phase 5 integration-seam gates are resolved. The linked
  migration recipe describes provider-first consumption; actual consumer
  adoption and staged compatibility qualification remain open.

### 2026-10-26 - Shared consumer seams reviewed and merged

- PR #9067 merged as `66b742c0cb9b7d2bdc888073836ce052394ef64e`
  after current-head Copilot approval and required green CI. Its intervening
  rebase did not change agent-index dependencies; the 150-test qualified
  composition and existing runtime guards remain applicable.
- Next implement identical-content write avoidance together with explicit
  keep-ID reconciliation. Timestamp-based cleanup must not delete reused
  chunks whose content timestamp was intentionally left unchanged.
  Continue exact hint consumption, hosted acquisition and bounded backfill;
  the two efforts' complete acceptance gates remain open.
- Release qualification found the already tracked artifact-build blocker
  #8983: run `40302569499`, job `121504300687`, refused `setup.py` as
  unsupported executable/alternate build configuration. Other executing
  gate/unit jobs passed. Existing recovery PR #9061 owns the admitted-source
  release repair; do not duplicate it or weaken artifact admission. These
  reviewed Dev merges are not yet qualified release/consumer-serving proof.

### 2026-10-26 - Canonical content no-ops and explicit file reconciliation

- Opted the shared file pipeline into strict, bounded canonical row comparison.
  Matching raw text and metadata retain their original timestamp and table
  version; model-only backfill no longer rewrites canonical content. Legacy
  direct store callers retain timestamp-refresh defaults and forced rebuilds
  retain explicit writes.
- Completed-file cleanup now receives the full keep-ID set, including empty
  files, rather than using timestamps as evidence of membership. Reads require
  complete file-ID enumeration; obsolete deletions are scoped and batched at 200
  IDs, retaining canonical retry authority until vector deletion succeeds.
  Cleanup and pair-receipt pruning still precede file checkpoint publication.
- Logical processed chunk totals remain compatible even when a content write
  is skipped. This is not yet the planned per-stage mutation counter API.
- The bounded native/recovery/consumer packet passed 233 tests. The final
  strict-comparison packet passed 24 tests after the bounded duplicate-count
  guard; helper type/lint checks and the runtime install contract passed.
  Controls cover both layouts, raw-text normalized-hash collisions, metadata,
  original timestamp/version preservation, new model pairs, retained/new chunks,
  empty files, failed reads, source/path isolation and partial vector cleanup.
- Phase 4's content/vector write-avoidance item is resolved. Exact hinted
  acquisition, hosted pagination recovery, unchanged-item model backfill,
  per-stage counters and logical/sliced maintenance remain next work; no
  release, consumer adoption or live-serving gate is claimed.
- Review found that same-line code declarations legitimately emit identical
  canonical chunk IDs. Coalesced exact input records before logical accounting,
  embedding and both incremental/forced writes, while rejecting conflicting
  metadata at one ID. Real JavaScript chunker regressions exercise both layouts;
  the expanded focused recovery packet passed 154 tests.
- Extended the same correction to legacy stored copies: stream duplicate rows
  in batches of at most 200, coalesce only exact persisted-field matches, and
  reject conflicting copies before mutation. Complete obsolete-ID enumeration
  deduplicates IDs rather than blocking cleanup of pre-change rows. Upgrade
  fixtures and the full composed packet passed 241 tests; bounded repair also
  exercises 506 copies of one canonical record.
- Advanced the file fingerprint policy to v2 so pre-coalescing v1 checkpoints
  cannot hide duplicate chunk accounting. Old checkpoints miss only when a file
  is next presented; verified chunk/model receipts remain reusable. The real
  same-line declaration fixture starts with a v1 checkpoint on both layouts.

### 2026-10-26 - Content reconciliation reviewed and merged

- PR #9070 merged as `c1ebf534ed4683d32e97b694a6702ee7d247be49`
  after current-head Copilot approval with no open findings and green CI.
  Final qualification passed 241 composed tests, 34 strict content/source
  integrity tests and 156 checkpoint-policy/recovery/reuse tests.
- Review corrections coalesce identical same-line chunks and exact historical
  stored copies while rejecting conflicting copies. The `file-v2` fingerprint
  policy requalifies an older file checkpoint only when that file is presented;
  valid chunk/model receipts remain reusable without a corpus-wide scan.
- The identical-content/vector-write and explicit keep-ID cleanup gate is
  resolved. Logical mutation counters, bounded unchanged-item profile backfill,
  hosted acquisition/recovery and local maintenance remain open.
- Continue actual scoped hint acquisition through the engine and connector.
  Published-source qualification and downstream adoption remain separate from
  this reviewed source merge; the existing release-repair owner remains intact.

### 2026-10-26 - Actual scoped hint acquisition implementation

- Expanded the existing engine's actual-callable v1 capability and preflighted
  exact configured source, connector acquisition support and effective account
  before opening stores or embedding clients. The original typed envelope
  reaches the selected connector unchanged; unsupported paths fail visibly
  rather than becoming polls or terminal partial successes.
- Built-in GitHub hints fetch only unique requested issue/PR identities and
  their complete fresh comment pages. Metadata revisions require API timestamps;
  wrong targets, stale revisions, incomplete comments, ref ranges and delivery
  annotations without item identities refuse rather than losing information.
- Event acquisition preserves the prior ordinary polling cursor, including
  null, and reuses existing pending-manifest recovery and queue retry authority.
  Native tests in both store layouts cover unchanged writes, engine readiness,
  duplicate/trailing bursts and failed-worker restart. Scoped acquisition at
  100, 10,000 and 100,000 unrelated path entries reads no unchanged paths.
- The initial provider/engine packet passed 98 tests; the expanded retained
  queue, transport, recovery, credentials and consumer packet passed 262.
  Final qualification and review follow. Git ref-range hints, external-provider
  hint acquisition, hosted page recovery and the full matrix remain open.
- Final acquisition/store compatibility qualification passed 242 tests,
  including cancellation before connector construction. Helper types, changed
  Python lint, runtime install contracts and documentation consistency pass.
- Review corrections accept canonical GitHub repository-name casing without
  admitting a different endpoint or item, and refresh the source-aware reader
  before no-op version assertions. A real changed-title control proves that
  the same assertion detects writes. The final review-fix packet passed 125.

### 2026-10-26 - Scoped work-item hints reviewed and merged

- PR #9076 merged as `ea2123bafc87b676d26194c74985e9cf259972de`
  after current-head Copilot approval with both findings resolved and green CI.
  Reconciled the existing source worktree; release and consumption are not
  implied. Retained orchestration tests also passed (85, one platform skip).
- Actual GitHub item identities now cross intake, queue, engine and connector
  boundaries, with real worker burst/restart tests. The broader changed-ID/ref-
  range and full built-in/external qualification gates remain open.
- Continue typed stage counts and bounded unchanged-item model backfill, then
  hosted page recovery and logical/local maintenance. The canonical Plan and
  Validation Plan remain the completion gate; both efforts are still Active.

### 2026-10-26 - Typed round work-count implementation slice

- Added versioned, JSON-safe `work_counts` to the existing engine result and
  worker statistics. Separate acquisition outputs, file/checkpoint reuse,
  per-model embedding calls/pairs, actual primary row upserts and canonical
  file-deletion IDs. Preserve legacy logical result totals and pending-crawl
  serialization. Extracted the routed batch writer while retaining the engine
  compatibility seam and its cancellation/failure behavior.
- Shared HTTP measurements count client-boundary attempts/retries, responses,
  decoded payload bytes, 304s and parsed JSON results. Unobserved transports
  remain `null`; semantic provider pages/bodies and physical storage I/O are
  not inferred. Cluster attempts/failures remain visible even when legacy
  maintenance catches an error; cached skips do not count as passes. FTS
  availability calls are not mislabeled as full builds.
- Native one/two-model fixtures in both layouts distinguish unchanged/replayed
  identities, metadata reuse, text edits, tombstones and interrupted-vector
  recovery. Real hosted-hint fixtures verify request counts and persisted
  worker statistics. Coupled bounded suites passed (172 and 160 tests);
  helper typing, Python lint and the 13-plugin install-contract guard passed.
- This establishes a measured subset, not the complete Phase 1 counters gate
  or the 100/10,000/100,000 acceptance matrix. Remaining provider-semantic,
  GC/derived physical-work and unchanged-item model-backfill qualification
  stays explicit; both Plan and Validation Plan remain open.

### 2026-10-26 - Work counts reviewed and merged; native reads qualified

- PR #9081 merged as `c54163c707b1ed6de8af621dcc9a106c7e68fccb`
  after current-head Copilot approval with no open findings and green CI.
  Reconciled the existing worktree; no release or consumer adoption is implied.
  The Plan and Validation Plan above remain the completion gate.
- Next, qualify bounded native reads for unchanged-item profile backfill before
  implementing a cursor. The installed backend supports native asynchronous
  `take_offsets` and pinned read-only table versions without the optional
  dataset dependency. Synthetic 100/10,000/100,000-row witnesses read two
  selected offsets after independent current-table deletion and reopening the
  pinned version. Native metrics reported two scanned rows/two requests for
  each size, with small but nonidentical payload reads; this is not proof of
  concurrent writer, retention or whole-backfill correctness.
- Also qualify actual selected-ID lookup I/O rather than inferring bounded
  storage work from a short predicate or small result. Keep physical-read and
  scalar-index preparation obligations explicit in the remaining matrix.
- Filed and claimed #9082 after public duplicate checks. A native two-ID query
  scanned the entire unindexed ID column at all three sizes; a prepared scalar
  index reduced selected data-row reads, but a subsequent append remained
  unindexed. The current native optimizer combines indexing with compaction
  and version pruning, so it is not used as an index-only updater.
- The separate Lance Python SDK exposes index-only `optimize_indices` with
  delta-index controls, but is not currently a store dependency. Qualify its
  compatibility, seasoning, platform support and bounded work before adoption;
  no dependency or live-state change is implied by this finding.

### 2026-10-26 - Budgeted native point-read implementation slice

- Under #9082, added explicit source-domain-owned scalar preparation and opt-in
  strict content/vector point reads. Ordinary calls retain their defaults;
  unprepared legacy stores are not silently migrated. This prepares a shared
  primitive for current-row backfill validation, not a second indexing loop.
- The caller budgets physical unindexed rows, including deleted rows. Existing
  indices receive only a new delta segment; a 32-segment ceiling requests
  explicit consolidation. Reads refuse missing/tail coverage and never build.
  Preparation preserves current source FTS coverage without refreshing stale
  FTS or changing existing retention. Manifest/index metadata costs remain real.
- Added seasoned host-only `pylance>=13.0.0,<14`; the selected composition is
  LanceDB 0.40.0, pylance 13.0.0 and lance-namespace 0.11.1. Linux native
  qualification is not Windows/macOS/ARM runtime qualification merely because
  wheels exist. Client/base dependency scope is unchanged.
- Native content/vector fixtures at 100/10,000/100,000 rows cover cold/warm
  point plans, append/update/delete and strict current records. Two requested
  IDs scan at most two data rows; per-tail SDK updates read less than 256 KiB
  in fewer than 32 operations at each size. Retained prior versions remain
  readable; forbidden combined compaction/pruning calls do not run.
- Bounded native integration selection passes 167 tests, including existing
  content, maintenance, source-domain, pair-reuse, consumer and count contracts.
  Lint, helper typing, module-size, documentation and install-contract guards
  pass. Python dependency seasoning passes all 387 declarations; the unrelated
  local npm audit cannot run without its Node/npm prerequisite.
- Review follow-up closes a preflight/writer-entry race: a known pre-effect
  refusal exits the writer normally before rethrowing, preserving an intervening
  committed append and a clean receipt. Initial-build/tail race regressions and
  post-effect failure controls retain existing dirty/uncertain recovery behavior.
- [Prepared chunk-ID documentation](../../../plugins/agent-index/docs/chunk-id-index.md)
  owns the API, limits and reproduction tests. Ordinary-round adoption,
  budgeted unchanged-item model backfill, logical mutation generations,
  hosted acquisition recovery and the full effort matrix remain open.
  No release, consumer consumption or live rollout is asserted.

### 2026-10-26 - Prepared point reads reviewed and merged

- PR #9102 merged as `38fa973a2e6b16c93fc023832e2dfd8a39669520`
  after current-head Copilot approval with no open issues and green required
  checks. The review's writer-entry race was fixed and independently re-reviewed;
  167 bounded native/shared tests pass. Reconciled the existing worktree and
  re-read the Plan and Validation Plan; the effort remains Active.
- Prepared point reads and explicit physical-row/segment budgeting are shared
  source primitives, not automatic ordinary-round adoption or completed model
  backfill. #9082 retains the wider native adoption/qualification scope.
- The prior release prerequisite changed: #9061 is merged and #8983 closed.
  Published Main `2b761442786c2067dd1091aa8e8f84c97c3417f1` contains the
  consumer injection seams and round work counts, with agent-index
  `0.10.96-dev1`. This does not imply that the just-merged #9102 primitive is
  already released, or that any consumer has adopted it.
- Next, consume a qualified published shared engine through the consumer's
  existing mechanical vendor path and remove duplicate orchestration; continue
  budgeted profile backfill and remaining acquisition/maintenance matrix work.
  Consumer replacement and live rollout retain their own safety gates.

### 2026-10-26 - Explicit consumer source-retirement ownership

- Consumer integration exposed a missing distinction: a configured crawl list
  need not be an authoritative inventory when the product also accepts pushed
  content. Added optional `SourceRetirementPolicy` to the shared reindex entry
  point, preserving ordinary defaults and the existing global GC safety valve.
- Consumers can retain sources outside their crawl selection, explicitly disable
  stale-generation collection, or supply a pure naming predicate and retained
  crawl-alias set. Exact deletion and pending-crawl/selected-store guards remain
  shared; no consumer-owned GC or orchestration loop is introduced.
- Failed full runs no longer advance the last successful full-reconciliation
  marker, collect stale generations or compact as if acquisition had succeeded.
  Existing cluster configuration supports consumer-owned background coalescing
  without an additional callback or global patch.
- Native tests cover actual engine retention of pushed content, full-run
  classification, crawl-cursor preservation, dry-run and pending-acquisition
  refusal, plus failed-full maintenance exclusion. This closes a consumer seam,
  not release/adoption, remaining acquisition/backfill or the effort matrix.

### 2026-10-26 - Source-retirement policy reviewed and merged

- PR #9112 merged as `8ff2500bc326896e8fe49d657fd626270ebbf22e`
  after exact-head Copilot approval with no open issues and successful required
  checks. Both review findings were fixed: explicit operator-retained source
  names precede custom classification, and failed-full tests assert recorded
  GC/compaction calls outside the legacy exception handlers.
- The final bounded selection passes 127 tests; retained pushed rows, pending
  acquisition exclusion, crawl aliases and no-scan disabled policy are native
  or direct regression controls. Re-read the effort Plan and Validation Plan;
  no broad matrix or actual-consumer checkbox is closed by this slice.
- Next, qualify a published source containing this policy through the actual
  consumer worker, then continue remaining bounded acquisition, model backfill
  and logical maintenance work. No release, consumption or rollout is implied.

### 2026-10-26 - Consumer repo naming without a forked FTS loop

- Actual-consumer validation found that generic repo-source identity parsing
  cannot represent every retained consumer naming convention. Added the pure
  `ContentStore.repo_filter_sql(repo)` override point, keeping the default
  generic identity and the shared guarded/sanitized FTS execution path.
- Native tests exercise the real indexed query with underscore, percent,
  apostrophe and backslash identities and a one-candidate limit. Consumers must
  escape values and match their vector-side policy; neither unbounded
  post-filtering nor a process-global helper replacement is used.
- The bounded repo-filter/FTS/currentness/consumer selection passes 39 tests.
  This is another staged consumer seam; actual published consumption and the
  remaining full effort matrix are still open.

### 2026-10-26 - Repo naming policy reviewed and merged

- PR #9119 merged as `6acc2e38ac4daac47324dbcddbdf055298c75e60`
  after exact-head Copilot approval with no open issues and green required
  checks. Strengthened the review's regression-test finding: distinct documents,
  both repo directions and both one-result and wide-result limits now reject
  early limiting and sibling wildcard leakage.
- The final bounded repo-policy/FTS/currentness/consumer selection passes 39
  tests. Reconciled the reusable worktree and re-read this effort's Plan and
  Validation Plan; remaining acquisition, model backfill, logical maintenance
  and complete consumer qualification are still open.
- Next, qualify a published source containing both reviewed consumer policies
  through the actual consumer path, rather than treating a development source
  overlay as consumed release evidence. Rollout retains its separate gate.

### 2026-10-26 - Prepared-index embedding coverage

- Actual released consumer qualification now uses the shared engine and both
  reviewed consumer policies rather than a development-source overlay. This
  does not close the separate rollout or full acquisition/backfill matrix.
- Added opt-in `bounded=True` to the existing embedding coverage selection and
  receipt verification methods. It requires prepared-index vector reads without
  changing defaults, building indices automatically or inventing a second
  coverage, cursor, reader or source ownership mechanism.
- Missing preparation, unindexed tails, backend failures and actual-vector
  mismatches refuse before new receipts are published. Existing raw-input and
  model identity, vector hashes, 200-ID batches and restart reuse remain intact.
- The bounded native/recovery packet passes 60 tests, including actual prepared
  coverage at 100, 10,000 and 100,000 vector rows, 505-pair batch boundaries,
  failed/late-incomplete verification, explicit tail preparation and clean
  refusal, profile changes, vector overwrites/loss and restart reuse. Changed
  Python lint/types and runtime install contracts pass.
- This prerequisite does not opt the ordinary embedding writer into bounded
  reads or implement unchanged-content backfill. Next factor the shared pair
  write seam with source-bound current-row checks, then qualify separately
  budgeted, resumable discovery. #4220, #8733 and #5302 retain their scopes.
