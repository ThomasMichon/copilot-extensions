# Reciprocal Projection Schema v2

Back to the [effort README](README.md).

## Objective

Make projection overflow truthful, byte-bounded, and stable under repeated
incremental updates without enumerating worktree records or the live
session-state root.

Schema v1 bounds retained relation count but increments `omitted_relations`
without retaining omitted identities. Once an identity is discarded, an
incremental writer cannot distinguish a new omission from a repeated update, so
the count can inflate indefinitely.

Schema v2 removes that impossible claim. Relation completeness and deletion-
fence completeness are explicit, independent, and sticky once lost.

## Constants and canonical encoding

- Maximum encoded projection size: `131072` bytes.
- Maximum retained relations: `128`.
- Maximum retained deletion tombstones: `128`.
- Encoding: UTF-8 JSON, lexicographically sorted object keys,
  `ensure_ascii=true`, separators `(",", ":")`, no indentation or other
  insignificant whitespace, and one trailing newline.
- Relation identity: `(project, worktree_id, role)`.
- Tombstone identity token: lowercase SHA-256 hex of the canonical JSON encoding
  of `[project, worktree_id, role]` using the same encoder settings without the
  trailing newline.

The digest is used only for conservative stale-write fencing. A collision can
block an unrelated projection update but cannot create or alter authoritative
worktree state.

## Invariants

- Worktree records remain the only relationship authority.
- Lifecycle writes target one exact known session ID.
- Ordinary writes never enumerate worktree records or the session-state root.
- Every encoded projection remains at or below `131072` bytes.
- Bound relations outrank nonterminal controller relations, which outrank
  terminal controller relations.
- Omission is not deletion and never creates a relation tombstone.
- Unknown future major versions remain byte-for-byte untouched.
- Restored projections remain read-only evidence after validation and become
  writable only through explicit local adoption.
- Lifecycle operations remain fail-open. A failed projection write is reported
  and leaves the sidecar unchanged; it is not reported as a successful
  projection update.

## Schema

```json
{
  "version": 2,
  "session_id": "<session-id>",
  "relations": [],
  "relation_tombstones": [
    {
      "key_sha256": "<64 lowercase hex characters>",
      "relation_revision": 42,
      "sequence": 7
    }
  ],
  "tombstone_sequence": 7,
  "history_complete": false,
  "overflow": true,
  "omitted_relations": null,
  "tombstone_overflow": false
}
```

Relation completeness:

- `history_complete: true` means the writer knows the projection began with the
  complete relation history and has never omitted a relation. Only fresh v2
  creation may assert it.
- `history_complete: false` means retained relations may still be validated
  individually, but absence from the projection proves nothing.
- `overflow: false` requires `omitted_relations: 0`.
- `overflow: true` requires `omitted_relations: null`.
- Every other v2 combination is invalid rather than coerced.
- `null` means the projection is known to be incomplete and makes no numerical
  claim about discarded relation identities.
- Once true, relation overflow cannot be cleared by an incremental write.

Deletion-fence completeness:

- `tombstone_overflow: false` means every deletion tombstone observed since
  projection creation or migration remains represented.
- `tombstone_overflow: true` means one or more older fences were evicted or
  could not be migrated.
- Once true, tombstone overflow cannot be cleared by an incremental write.
- Tombstone overflow does not by itself imply that retained relations are
  incomplete. Consumers expose it as a separate diagnostic.

`tombstone_sequence` is projection-local and monotonic. It gives tombstones from
different worktree records a comparable retention order without treating their
per-relation revisions as a global clock.

## Relation updates and retention

For an incoming relation:

1. Compare it with a retained relation using the complete current revision-
   vector ordering: reject an incoming vector when the retained vector is newer
   or the vectors are incomparable; accept equal vectors; and accept a strictly
   dominating incoming vector for understood-key replacement. Every accepted
   update preserves retained additive unknown fields. When either vector is
   incomplete, retain the current fallback: reject a lower incoming relation
   revision and otherwise merge conservatively. Compare the relation revision
   separately with the matching digest tombstone.
   A tombstone rejects an upsert whose relation revision is less than or equal
   to the tombstone revision. Legitimate reassignment must strictly advance.
2. Merge additive same-schema fields.
3. Add or replace it in the candidate relation set.
4. Sort candidates by:
   1. bound relation;
   2. nonterminal controller relation;
   3. terminal controller relation;
   4. descending relation revision;
   5. ascending `(project, worktree_id, role)`.
5. Retain a deterministic priority prefix under the byte budget.

An omitted relation is not negatively cached. A later update carries the full
current relation again and can displace lower-priority retained relations.

If prior relation overflow is false and every candidate fits, the envelope
remains `overflow: false`, `omitted_relations: 0`; `history_complete` retains
its prior value. If a candidate is excluded, or prior overflow is already true,
emit `history_complete: false`, `overflow: true`,
`omitted_relations: null`. Reprocessing the same omitted relation then produces
identical bytes rather than incrementing a counter.

A bound relation that cannot fit causes an explicit bounded-write failure. The
writer does not publish an apparently useful projection without its primary
recovery relation.

## Deletion tombstones

Authoritative relation removal:

1. Removes the retained relation when present.
2. Hashes its canonical identity.
3. Creates or advances the matching tombstone only when the removal revision
   strictly advances.
4. Increments the projection's `tombstone_sequence` counter once and assigns
   the new value to that changed tombstone.
5. Retains the 128 highest sequence values, breaking ties by ascending digest.
6. Sets `tombstone_overflow: true` if any tombstone is evicted.

Repeated removal at the same revision is a semantic no-op. A newer
authoritative upsert removes the matching tombstone before normal retention.
Evicted fencing may permit a stale projection entry to reappear, but consumers
still validate it against authoritative worktree state before navigation,
repair, or restored-state trust.

## Byte-budget selection

The final encoded bytes are authoritative. Count caps are secondary guards.

1. Compact tombstones to the deterministic highest-sequence 128. The compact
   set is included in the actual encoded-size calculation.
2. Apply the hard 128-relation cap to the sorted candidates. Exceeding the count
   cap is an exclusion and makes the relation set incomplete.
3. When prior relation overflow is false and the count cap excluded nothing,
   first encode the complete candidate set with `overflow: false`,
   `omitted_relations: 0`. If it fits, retain it.
4. Otherwise start selection with the pessimistic relation envelope
   (`overflow: true`, `omitted_relations: null`) and the current history and
   tombstone-completeness flags.
5. Add at most 128 candidates in priority order while canonical encoding remains at or
   below `131072` bytes.
6. Stop at the first candidate that does not fit and omit it plus every lower-
   priority candidate. This preserves a deterministic priority prefix.
7. Serialize retained relations in the existing ascending
   revision-and-identity order.
8. Every tombstone addition re-runs relation selection because fencing growth
   may displace the lowest-priority retained relations.
9. If the fixed envelope plus compact tombstones or the bound relation cannot
   fit, refuse the write and leave the prior sidecar unchanged.

The implementation carries a maximum-shape fixture with 128 realistic
relations and 128 compact tombstones and proves the canonical document remains
within `131072` bytes.

## Creation, recovery, and migration

Completeness can be asserted only when the writer knows it started with the
whole projection history:

- Fresh projection creation starts with `history_complete: true` only when the
  caller supplies an explicit initial-registration creation mode and the
  authoritative record proves this is the session's first relation. Every other
  missing-file write uses conservative reconstruction mode.
- Every v1 projection migrates with `history_complete: false`, because v1 has no
  durable evidence distinguishing uninterrupted history from a prior
  incremental rebuild or truncation.
- A valid, non-overflowed v1 projection may migrate with relation overflow
  false, but absence from its relation set is not authoritative.
- A v1 projection with overflow migrates to `overflow: true` and
  `omitted_relations: null`; its inflated count is discarded.
- Valid v1 full-key tombstones migrate in array order to digest tombstones with
  projection-local sequence values `1..N`; `tombstone_sequence` initializes to
  `N`.
- V1 tombstones beyond 128 retain the final 128 array entries and set
  `tombstone_overflow: true`. Malformed entries are dropped and set the same
  flag.
- A missing, corrupt, or oversized projection rebuilt from one incremental
  relation starts with `history_complete: false`, `overflow: false`,
  `omitted_relations: 0`, and `tombstone_overflow: true`; prior relation and
  deletion history is unknown, but the current retention pass omitted no known
  candidate.
- Restored or synchronized trees remain read-only through migration and
  validation. V2 does not add or imply a restored-session adoption transition.
- A v1 writer leaves v2 untouched as an unsupported newer schema.
- A v2 writer leaves every future major version untouched.
- V1-to-v2 migrate-on-write intentionally rewrites the canonical bytes even
  when retained relation semantics are unchanged; the one-time rewrite is the
  schema transition, after which ordinary semantic no-op suppression resumes.

V2 intentionally provides no incremental overflow-clearing or history-
completion path. Complete authoritative reconstruction across projects and
machines would require a separate reverse index with an explicit scope; it must
not be implied by ordinary backfill or doctoring. Rebuildability means the
projection can always be recreated as conservative, individually validated
evidence, not that completeness can always be recovered.

## Two-stage mixed-writer rollout

V2 emission is gated by release:

1. **Reader release:** ship v2 parsing, validation, in-memory v1 migration,
   machine-readable normalization, and v2 fixtures while continuing to emit v1.
2. Deploy that release to every supported writer machine and complete the
   Windows, Linux, restored/synchronized, and remote-controller reader matrix.
3. **Writer release:** enable v2 creation and migrate-on-write only after the
   reader floor is recorded in the effort.

A machine below the reader floor, or a stage-1 reader machine that has not yet
received the writer release, may encounter synchronized v2 evidence. Its
lifecycle operation still succeeds, but the projection update is reported
blocked and leaves the file byte-identical. Normal projection maintenance
resumes only after that machine receives the writer release.

## Consumer contract

`session-recovery`, `session-lineage`, controller-lineage resolution,
`backfill-sessions`, `doctor`, and resident reconciliation normalize v1 and v2
into:

- schema version;
- current, missing, invalid, unsupported, or relation-set-incomplete status;
- history-completeness boolean;
- relation overflow boolean;
- omitted-relation count (`null` for v2 overflow);
- tombstone-overflow diagnostic;
- retained relations and deletion tombstones.

Every named JSON and human-readable surface accepts and renders
`omitted_relations: null` without an exception or misleading numeric count.
Retained relations remain individually validated against authoritative records
even when the set is incomplete. Set-level absence, uniqueness, and complete-
controller claims remain report-only. V2 tombstones normalize as opaque digest,
revision, and sequence records; they do not claim to render the removed
identity. Tombstone overflow remains visible but does not hide otherwise
validated retained relations. Rescue and synchronization carry v2 opaquely;
restored readers apply the same version, completeness, and provenance rules
after import.

## Validation matrix

- Canonical encoder byte-for-byte fixtures on Windows and POSIX.
- V1 non-overflow and overflow migration, including ordered full-key tombstone
  hashing and malformed-tombstone degradation.
- Every v1 migration is history-unknown; fresh v2 registration is the only
  complete-history creation path.
- Reader release continues to emit v1.
- V1 writer refusal to replace v2; v2 refusal to replace a future major.
- Maximum retained relations and compact tombstones fit `131072` bytes.
- Long valid identities and escaped content trigger deterministic byte-prefix
  retention rather than encode failure.
- Bound and nonterminal controller relations win retention over terminal
  controller relations.
- Repeated omitted updates do not change bytes.
- An omitted relation can be retained when its priority changes.
- Incremental relation removal does not clear prior relation overflow.
- Tombstone updates are ordered by projection sequence, not incomparable
  per-worktree revisions.
- Tombstone overflow retains the deterministic latest 128, marks only fence
  incompleteness, rejects covered stale upserts, and permits newer reassignment.
- Missing/corrupt incremental reconstruction starts conservatively incomplete.
- Under a fixed tombstone set, the same post-revision-arbitration relation
  candidate set and prior completeness flags produce the same retained prefix
  and encoded bytes regardless of candidate input order.
- Tombstone sequence assignment and bytes are intentionally removal-order
  dependent; repeated identical removal history remains byte-stable.
- All recovery, lineage, controller, doctor/backfill, and rescue/sync surfaces
  render v2 null overflow, history completeness, opaque tombstones, and
  tombstone overflow safely while validating retained relations individually.
- Restored validation never enables a migration write.
- Writer emission remains blocked until the reader floor and cross-platform
  matrix are recorded.
