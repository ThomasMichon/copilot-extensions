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
  — the closed #4220/#5302 tracking tokens retain their unfinished requirements
  under [#5768's receiving effort](../agent-index-standalone-service/README.md),
  sections I4220 and I5302; closure does not certify implementation or data repair.

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

The incumbent effort and retrieval-slice claimants retain their work and every
source/resource obligation. After any separately verified tracking supersession,
#9023 remains historical provenance, not a required closed live claim token.
Continuing coordination uses this same Active effort's Plan, Validation Plan
and Journal plus eligible OPEN bounded-slice issues under those same owners.
No claim expiry, reassignment, new implementation admission or rollout is
created by this planning intake.

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

- [x] Specify an additive, versioned change-set: canonical source/account scope,
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
- [x] Establish per-stage counters and tiny deterministic fixtures before
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
  source isolation and compatibility with #4222/#4220. The bounded native
  canonical-alias revert qualification is tracked in #14684; broader ownership
  acceptance remains with the existing receiving effort.

### Phase 3 - Delta-bounded discovery and reconciliation

- [x] Carry exact changed IDs/ref ranges through intake, queue and connector
  boundaries; merge duplicate deliveries without erasing distinct changes.
- [x] Make local Git use commit-range changes and tombstones without enumerating
  all tracked paths and commit history on each ordinary round.
- [ ] Bound hosted issue/PR acquisition and timestamp overlap replay by cursor
  and identity. Provider limitations must remain explicit; the
  [Azure DevOps proposal](azure-devops-acquisition.md) stages query-qualified
  exact identities before broader polling and repair coverage (#13363).
  Its [point-component contract proposal](azure-devops-point-targets.md)
  separates named target coverage from combined-document replacement (#13386);
  typed intake now retains parked v2 claims with v1-only dequeue isolation and
  explicit whole-envelope pinning that separates trailing arrivals. An explicit
  claim-bound pending envelope retains that entire snapshot with exact prepared
  parent/incarnation checks; ordinary readers and premature retirement refuse it.
  A disconnected named-point reader uses exact revision/comment-version APIs and
  rechecks query/account context without acquiring the combined discussion;
  explicit pending-parent retention can resume each exact target and compose a
  whole-claim candidate only after every named version/correlation is retained.
  An explicit prepared-journal binding requalifies and retains that exact complete
  application candidate without replacing the pending parent. Explicit
  `PreparedApplication` consumption now requalifies that complete binding before
  chunking/embedding and uses scoped native vector reads; ordinary point-domain
  execution remains open; the explicit acquisition caller is described below.
  A [retained component carrier](../../../plugins/agent-index/docs/architecture.md#component-recovery-carrier-application-unsupported)
  preserves ordering inputs behind pre-application refusal, including changed
  replacement and premature-completion refusal. Pure content/version comparison
  does not qualify durable application or deletion evidence.
  [Raw file-point observations](../../../plugins/agent-index/docs/file-projection.md)
  provide a separately budgeted native snapshot prerequisite, not applied-version
  or deletion proof. The opt-in
  [native component ledger](../../../plugins/agent-index/docs/component-ledger.md)
  retains scoped ordering/tombstone observations with missing-history refusal and
  binding-aware bounded consolidation; it is not an applied-content certificate.
  [Prepared work](../../../plugins/agent-index/docs/prepared-application.md)
  also binds its exact acquired manifest, payload, mode and scope before cache
  mutation. The explicit
  [native application](../../../plugins/agent-index/docs/component-ledger.md#explicit-native-application)
  composes monotonic point writes with prepared/routed coverage and path
  checkpoints, retaining pending work and refusing uncertain state.
  Applied snapshots qualify the existing native/checkpoint/routed-coverage
  owners, retaining optional typed acquisition identity without enriching
  legacy records. A separate exact-scope point-domain binding and default
  reader refusal are implemented, with an explicit
  [named-component read](../../../plugins/agent-index/docs/component-ledger.md#selected-named-component-reads)
  through the existing qualification owners. Default query/HTTP/MCP/CLI
  frontdoors and direct native content/vector/cluster/package-export readers
  have bounded refusal qualification; broader consumer composition remains
  open. Exact-scope cold content/model schema and component-ledger bootstrap,
  plus explicitly budgeted point-index preparation, typed-claim application
  and its exact sealed publication suffix, are available. A separate explicit
  whole-claim completion composes current queue/pending/native/checkpoint owners
  with requalifying lost-return retries. An explicit
  [retained worker](../../../plugins/agent-index/docs/component-ledger.md#explicit-retained-point-worker)
  composes fully acquired claims through prepared/native application and that
  completion under the existing source writer. An explicit
  [Azure DevOps acquisition caller](../../../plugins/agent-index/docs/component-ledger.md#explicit-azure-devops-acquisition-caller)
  composes missing named reads with fresh eligibility checks, retained recovery
  and that worker; an explicit
  [configured composition](../../../plugins/agent-index/docs/component-ledger.md#configured-in-process-azure-devops-composition)
  opens isolated prepared owners and pins the expected whole parked snapshot.
  An explicit [in-process worker hook](../../../plugins/agent-index/docs/component-ledger.md#explicit-in-process-worker-dispatch)
  selects that bound composition without detached-worker registration and verifies
  completion through the queue rather than the callable's return value.
  An explicit [async lifetime owner](../../../plugins/agent-index/docs/component-ledger.md#explicit-async-lifetime-owner)
  joins native dispatch through cancellation and truthful bounded drains,
  with supported point queue cancellation serialized against that same source
  writer. An explicit [point queue consumer](../../../plugins/agent-index/docs/component-ledger.md#explicit-point-queue-consumer)
  supplies exact-scope FIFO batches with failure pause and explicit retained
  retry. An explicit [service binding](../../../plugins/agent-index/docs/component-ledger.md#explicit-service-binding)
  composes isolated exact-scope HTTP intake, the existing governed runner and
  joined client lifetime, with paused reconstruction and no ordinary activation.
  The normal Python `server.serve` entrypoint accepts that same optional factory;
  default CLI selection and explicit activation remain unchanged.
  Fresh entirely superseded claims can now select qualified higher-version
  completion before named-body acquisition; equal versions retain canonical
  comparison, mixed claims retain whole acquisition and existing pending work
  is never bypassed. Explicit HTTP/service qualification covers that distinct
  no-acquisition outcome and its client-free completed retry.
  The [configured executor ownership step](azure-devops-point-targets.md#configured-executor-ownership-before-caller-migration)
  supplies production connector/model lifetime composition under that existing
  Python binding, with per-execution missing-pair readiness and cancellation-safe
  joined cleanup.
  The [selected executor/qualified-reader refinement](azure-devops-point-targets.md#selected-executor-and-qualified-reader-composition)
  now composes both actual owners through one shared in-process binding, with
  per-read non-creating qualification and joined shutdown. The
  [reviewed running-service declaration](azure-devops-point-targets.md#running-service-declaration-and-provider-rollback)
  now has receiver-only selected-pair/lifecycle advertisement and pure optional
  missing-support/provider-rollback validation. Existing clients remain unchanged;
  the [request-bound required-admission refinement](point-required-admission.md)
  was reviewed in #13712 and its receiver merged in #13715. It checks the actual peer,
  selected scope and admission before an owned queue effect and emits an
  explicit typed receipt. Existing ordinary wire callers remain unchanged; the
  [explicit caller proposal](point-required-admission.md#explicit-caller-migration-proposal)
  was reviewed in #13717. Its opt-in `AgentIndexClient.reindex_points` checks
  selected-peer support before submission and validates the typed admission
  receipt without replay after unknown acceptance; implementation merged in
  #13719. No ordinary caller migrates.
  Ordinary v2 execution and automatic partial-effect/restore
  reconciliation remain unimplemented; live-provider, broader event-delivery
  and consumer-scale qualification remain open.
- [ ] Persist acquisition progress so retry/restart does not restart a
  corpus-wide listing. The bounded opt-in GitHub hinted-item slice
  [#14679](https://github.com/ThomasMichon/copilot-extensions/issues/14679)
  uses the existing page journal; larger discussions and full repair stay open.
- [ ] Move authoritative full enumeration into separately budgeted, resumable
  reconciliation slices. A missing checkpoint or expired cursor requests
  visible repair; it must not disguise an unlimited rebuild as a tiny update.

#### Explicit caller acquisition requirements

Issue [#13392](https://github.com/ThomasMichon/copilot-extensions/issues/13392)
owns the reviewed **agent-recommended, opt-in in-process requirement**.
The [consumer contract](../../../plugins/agent-index/docs/consumer-migration.md#requiring-page-acquisition)
documents `require_paged_acquisition`, selected-callable negotiation and retained
v7 page provenance; legacy/default producers and all existing source hashes
remain unchanged.
The existing collector prefers page protocols but otherwise permits legacy
`list_paths()` / `discover_changed()` acquisition. Give a consumer a way to
require the declared paged protocol instead of silently accepting that fallback;
retain compatibility behavior for callers that do not opt in.

Use the existing engine/collector preflight and selected-callable capability
owners. A required incremental round selects `AcquisitionPage` v1; a required
hinted round also retains negotiated hints v1 and its immutable claim; a required
full round selects `SnapshotPage` v1. Unsupported or malformed selections fail
visibly before acquisition or application of unqualified retained work. Selecting
a protocol is not new proof of provider coverage, finite nested HTTP work,
bounded whole-manifest memory or payload bytes. Existing provider qualification
and explicit repair requirements remain independent.

The realization must preserve source/account and pending-state ownership,
polling checkpoints, source-scope hashes and old callable behavior. Do not add a
caller policy to `SourceSpec`'s hashed source identity. Negotiate support on the
actual selected indexing callable before an opted-in caller supplies the new
requirement; an old callable must not silently ignore it. Keep this initial
slice in-process: new service/CLI/provider wire forms require their own
contract-evolution review, not incidental forwarding.

Focused controls must exercise the actual engine plus the existing collector:
compatible legacy calls still work; a required call cannot invoke legacy
listing/discovery; all three admitted page forms retain their scope and cursor
semantics; malformed capability declarations refuse; and incompatible
pending/ready replay cannot evade preflight, discard progress or mutate the
indexed projection. Reopen retained state in the recovery cases. Qualify new
caller/new engine and old caller/new engine behavior; an unsupported old selected
callable refuses the new requirement. Put only small contract cases in required
CI. Preserve both parent plans, the #13386 Azure DevOps coverage gate and
separately budgeted reconciliation; this proposal authorizes no live operation.

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
- [x] Publish a consumer migration recipe and staged compatibility tests.
  Consumers re-sync the shared engine and remove duplicated orchestration;
  adapter-only patches are not a substitute for shared fixes.
- [x] Qualify release, actual consumed source and a separately authorized
  rollout. Keep merged, released, consumed and serving evidence distinct.

- [x] GP1 - Give shell-style file-path globs the same matching meaning in shared
  FTS and hydrated hybrid results, including wildcard and literal-path cases.
  Preserve source/repo/metadata filters; specify behavior rather than requiring
  a particular SQL or Python converter.
- [x] GP2 - Qualify matching eligibility and final result limits within the
  finite candidate window. Nonmatches must not consume final eligible slots;
  disclose window-induced underfill rather than claiming exhaustive top-N or
  adding an unbounded scan. FTS and hybrid rankings need not be identical.
- [x] GP3 - Keep this retrieval slice independent of acquisition, checkpoints,
  deletion/reconciliation, upserts and embedding policy. Preserve the existing
  #4222/#4220/#8733/#5302 owners and separate source, consumer, native, release
  and serving evidence.

## Validation Plan

- [x] Resolve ALL GP1-GP3 retrieval Plan clauses AND the GPV1-GPV3 technical
  cases in [validation.md](validation.md#cases). Keep every existing count-based,
  correctness-owner and consumer/rollout gate; this bounded retrieval check
  neither substitutes for them nor waits for whole-engine completion.

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

### 2026-11-25 - Explicit point binding through service entry

The [service-entry journal](journal/2026/11.25%20point-service-entry.md)
records the existing factory's synchronous entrypoint composition, native
active/passive startup and invalid-binding refusal. This is not a serialized
selector, ordinary CLI migration, automatic activation or parent completion.

### 2026-11-25 - Native alias-revert recovery qualification

The [native alias journal](journal/2026/11.25%20native-alias-recovery.md)
records #14684's shared-store A/B/C control through separate actual workers,
pre-acquisition refusal and explicit synthetic repair, merged in #14685.
It does not complete the broader source-ownership gate or either parent's
ten open items.

### 2026-11-25 - Bounded GitHub hinted acquisition

The [hinted-acquisition journal](journal/2026/11.25%20github-hinted-acquisition.md)
records #14679's explicit connector opt-in, existing journal composition and
single-comment-page bound, merged in #14680. Default callers, full
reconciliation and the ten open parent Plan/Validation items remain unchanged.

### 2026-11-25 - Explicit point client delivered

The [client-delivery journal](journal/2026/11.25%20point-client-delivery.md)
records merged #13719, lossless shared-inventory reconciliation, current-head
review and actual unit CI, followed by explicit-client native acquisition and
replay counts at three corpus sizes, merged in #14674. Existing callers, source
activation and rollout remain unchanged. The canonical Plan and Validation Plan
retain ten open items; bounded source coverage, repair and consumer evidence
remain separate gates.

### 2026-11-20 - Named-point native corpus counts

The [native-corpus journal](journal/2026/11.20%20point-native-scale.md)
records actual queue/caller/worker/reader operation-count qualification beside
100/10,000/100,000 unrelated content/vector/checkpoint rows, and separately
observed process-byte growth. Additional fixture setup populates the same
unrelated ordering-ledger cardinalities and preserves sampled raw records;
broader acceptance remains open. Merged #13678 supplies the initial native
corpus qualification, not constant total-cost or consumer acceptance.
The same journal now records a separate no-replay completed-retry control and
per-query/index-inspection attribution. Representative B-tree plans preserve
bounded canonical rows while process bytes grow; no production cache, currentness
shortcut or ordinary activation is justified by these observations.
Merged #13683 supplies that fixture-only attribution; the broader count/consumer
matrix remains open.
Merged #13685 supplies unrelated ordering-ledger scale with sampled raw-record
preservation. The separate HTTP/service scale packet now exercises the existing
higher-covered path and client-free completed retry at those same cardinalities,
with equal logical/native query metrics and separately reported process-byte
growth. Ordinary activation, automatic repair and broader acceptance remain open.
Merged #13689 supplies that HTTP/service packet after current-head approval
and actual unit CI. Next, inspect the explicit selected-runtime capability seam
against the reviewed point-target contract before choosing further source wiring;
successful fixture composition is not blanket v2 execution advertisement.

### 2026-11-20 - Point-worker dispatch and lifetime

The [worker-lifetime journal](journal/2026/11.20%20point-worker-lifetime.md)
records merged #13646, original-selection verification, current-head review,
executed/reused CI evidence and guarded source reconciliation. Both parent
completion gates remain open; hosted lifetime and ordinary scheduling remain
separate implementation work.
The same entry now records the explicit async host's joined cancellation and
truthful drain tests; service/queue admission and cutover wiring remain open.
Merged #13649 supplies that host after current-head approval and passing CI;
its source reconciliation and remaining integration boundary are journaled.
Merged #13651 serializes supported point cancellation with native ownership;
merged #13655 adds caller-driven exact-scope queue batches with failure pause.
Merged #13659 composes explicit service construction, isolated exact-scope
HTTP intake, the existing governed runner and joined client lifetime.
Ordinary activation, cross-generation recovery and broader acceptance remain
separate.
Merged #13665 qualifies explicit service reconstruction around sealed native
publication and unchanged refusal of an unsealed partial write.
Merged #13672 skips acquisition for entirely higher-covered fresh claims;
merged #13676 qualifies that outcome through actual HTTP/service dispatch and
client-free completed retry. Unrelated-corpus scale qualification remains open.

Append-only entries are preserved in dated files, newest first.

- [2026-11-19 retained point model/table ownership](journal/2026/11.19%20point-model-binding.md)
- [2026-11-19 caught nested source effects](journal/2026/11.19%20nested-writer-latch.md)
- [2026-11-19 point-domain binding and read refusal](journal/2026/11.19%20point-read-domains.md)
- [2026-11-19 component application prerequisites](journal/2026/11.19%20component-application.md)
- [2026-11-18 Azure DevOps response and selected-record coverage](journal/2026/11.18%20ado-response-coverage.md)
- [2026-11-18 Azure DevOps body-batch completeness](journal/2026/11.18%20ado-body-batches.md)
- [2026-11-18 Azure DevOps PR update-contract refusal](journal/2026/11.18%20ado-pr-update-contract.md)
- [2026-11-18 Azure DevOps PR body completeness](journal/2026/11.18%20ado-pr-body-completeness.md)
- [2026-11-16 explicit-backfill readiness](journal/2026/11.16%20backfill-readiness.md)
- [2026-11-15 native Windows retrieval qualification](journal/2026/11.15%20windows-retrieval-qualification.md)
- [2026-11-14 retrieval-glob intake](journal/2026/11.14-retrieval-glob-intake.md)
- [2026-11-09 acceptance reconciliation](journal/2026/11.09%20acceptance-reconciliation.md)
- [2026-10-27 checkpoint recovery](journal/2026/10.27%20checkpoint-recovery.md)
- [2026-10-27 provider measurements](journal/2026/10.27%20provider-work-measurements.md)
- [2026-10-26 storage](journal/2026/10.26%20incremental-indexing-storage.md)
- [2026-10-26](journal/2026/10.26%20incremental-indexing.md)
- [2026-10-25](journal/2026/10.25%20incremental-indexing.md)
