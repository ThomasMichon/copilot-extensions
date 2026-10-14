# Authorized-store migration and serving eligibility

Back to [the standalone index effort](README.md). This is an
**agent-recommended proposal**, not an implemented migration or permission to
modify an incumbent store. #6383 owns the index store/indexing slice;
#6240/#6030 retain application lifecycle and deployment authority.

## Measured blocker

The real-store fixture contains 140,000 authorized candidates across 18 synthetic
families. Legacy vector tables have no authoritative source projection, so
pre-retrieval authorization requires many bounded ID-partition searches.
The candidate implementation preserves bounded memory and authorization but
does not satisfy the Gateway's existing five-second HTTP hop or leave enough
headroom within the thirty-second execution grant.

Post-ingestion/setup measurements, without tracemalloc, using a warm two-dimensional
inference fixture:

| Query | Legacy full grant | Scalar-prepared full grant |
| --- | ---: | ---: |
| Vector | 23.000214 s | 13.747612 s |
| FTS | 32.214991 s | 15.098915 s |
| Hybrid | 56.745131 s | 28.031588 s |

Scalar preparation is idempotent and not performed during ordinary reads.
Empty and FTS queries make no inference call; nonempty vector/hybrid queries
make one. These are implementation measurements, not real-model live-transport
or production eligibility proof. Merely extending deadlines/grant lifetimes or
raising a candidate limit does not close this blocker.

## Proposed realization

Prefer an authoritative source projection on vector rows, or a supported
store-native authorized join with equivalent guarantees. Choose the exact
mechanism after reviewing the actual writer/store interfaces; do not create a
second authoritative corpus or guess source identity from caller labels.

- Source identity comes from canonical persisted chunk provenance. Preserve
  exact family/colon-descendant semantics, model identity, chunk IDs and vectors.
  Missing, conflicting or orphaned provenance is explicit, never reassigned to
  a guessed source or exposed through an unrestricted fallback.
- New ingestion and incremental updates maintain the projection at the owning
  writer. Deletion, source changes and interrupted tasks cannot leave a stale
  authorization label that another principal can use.
- Existing data uses a bounded, restartable lifecycle migration with current
  schema/generation evidence and existing operation/receipt contracts. Ordinary
  search requests do not mutate schemas or build indexes.
- Fence the actual writer before snapshot/backfill/activation; resolve concurrent
  changes explicitly. An in-memory lock, healthy replacement, or completed file
  copy does not exclude another process.
- Activate only a verified complete projection. Prepared/candidate/active/ready
  remain distinct. Unprepared opted-in serving fails explicitly rather than
  advertising full-grant readiness from a slow legacy fallback.
- Keep unselected native behavior and supported clients compatible. Declare
  binary/data/protocol rollback boundaries, retained recovery targets and
  commit-forward behavior before admitting irreversible writes.
- Source predicates apply inside native vector/FTS candidate retrieval and
  hydration, before ranking/top-k. Empty authorization is never unrestricted.
  Preserve deterministic results and configured corpus isolation.

## Review and validation gates

- [ ] Agree the projection/join, writer fence, migration receipts and rollback
  contract with the existing lifecycle owners before implementation.
- [ ] Prove cold creation, old-store upgrade, unchanged rerun and bounded resume
  after interruption, without duplicate writers or ordinary-read mutation.
- [ ] Preserve chunk/vector identity and canonical source provenance; exercise
  deletes, changed sources, missing provenance, conflicting generations and
  concurrent writes.
- [ ] Prove compatible rollback or explicit irreversible-downgrade refusal;
  do not restore an old authority or erase acknowledged writes.
- [ ] Exercise at least 140,000 candidates across 18 synthetic families with
  forbidden lookalikes, omitted/explicit filters, empty grants and wrong corpus.
- [ ] Compare vector/FTS/hybrid top-k and hydration against the authorized
  reference, with bounded predicates/memory and no forbidden retrieval.
- [ ] Measure individual post-setup requests with real model/transport headroom
  inside the existing five-second Gateway hop; fixture setup and instrumented
  composite duration are separate evidence.
- [ ] Prove the actual CLI/Gateway/container path only after the store is eligible.
  A passing local migration test is not live offload or full-corpus parity.
