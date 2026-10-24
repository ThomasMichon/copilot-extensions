# Lossless contract provenance layout

Back to the [effort](README.md). **Prospective, agent-recommended design.**
Source-only format work is authorized; this contract still requires normal
proposal review before implementation. It does not authorize runtime changes.

## Problem and scope

The Bridge contract registry must retain real historical source evidence while
remaining below the existing per-file size guard. Minimal JSON formatting no
longer suffices: reconciling the actual base and #8389 source histories retains
1,853 provenance records and requires 1,060,609 bytes, 12,033 over the unchanged
1 MiB limit before another capture.

Preserve every record, its field values and semantic sequence. Do not delete,
sample, deduplicate, reorder or rewrite evidence to fit the file. Do not raise
the guard, weaken source/capture verification or substitute a newer source for
an unqualified historical observation.

This closes the effort's source-evidence prerequisite, not custody or restore
qualification. HTTP/native generations, capability floors, durable runtime
state, installer behavior and operational permissions do not change.

## Existing owner, versioned representation

Extend the current contract registry, schema, checker and capture owners.
Do not introduce another registry, service, scheduler or evidence authority.
Names below describe proposed data, not an installed schema or command.

- Add an explicitly versioned registry representation supporting an ordered
  historical provenance prefix in immutable JSON sidecars plus the existing
  inline appendable provenance suffix.
- Each descriptor identifies a contract, confined relative JSON path, content
  SHA-256 and exact record count. Each sidecar identifies its own schema and
  contract and contains only provenance records, not further references.
- Bound each artifact below the unchanged guard. Use deterministic,
  content-addressed sidecar identities and canonical encoding. An existing path
  is reusable only for identical verified bytes; never overwrite a different
  historical artifact.
- Materializing the ordered prefix and inline suffix must reproduce the
  complete original record sequence exactly. The index remains the one reviewed
  owner of the references; sidecar hashes establish integrity, not permission.
- Current-format readers continue to accept the prior inline representation.
  Updated readers reject unsupported versions or incomplete sidecars explicitly;
  they never return only the inline suffix as complete history.
- Establish the behavior of the known older canonical readers/writers before
  claiming compatibility. An unsupported consumer remains unsupported; a
  version marker alone is not proof that an old tool checks it.

The implementation may choose bounded chunk sizes and helper/API names after
tracing the real call graph. It must preserve these invariants and record the
actual reader/writer inventory before declaring the slice complete.

## Reader, writer and evidence closure

Inventory and update every canonical consumer of the registry, including:

| Owner | Required behavior |
|-------|-------------------|
| Schema and contract checker | Validate the index, every referenced artifact and the complete materialized provenance through existing semantic/source checks |
| HTTP-45/46/47 capture owners | Preserve archived prefix and inline suffix, append genuine observations once, and keep their declared output ownership and immutable-history checks |
| Source snapshot and execution-closure validation | Bind referenced metadata inputs where required; verify completeness against the actual captured source, including orphaned-checkpoint fallback |
| CI evidence preparation | Preserve its exact-source, trusted-repository fetch boundary; validation itself remains local/read-only |
| Fixture seed/replay and other discovered consumers | Carry referenced artifacts and inspect the complete record set; no materialized-history loss or success-shaped omission |

One shared codec/reader should own materialization and integrity checks rather
than scattering subtly different decoders across these callers. Keep semantic
provenance validation in the existing contract owner, not a second authority.
Update directly related documentation and test fixtures together.

Migration/publication belongs to the existing explicit source-evidence write
flow. It must prove lossless conversion before publishing the new index, stage
only its declared artifacts, preserve historical packet bytes, and retain
recoverable old/new input if publication is interrupted. A read-only checker,
inspection or CI fetch must not silently migrate, recapture or repair metadata.

Normal captures may extend the inline suffix or publish additional immutable
chunks through that same declared owner. They cannot mutate earlier chunks,
invent provenance or make missing historical evidence look current.

## Integrity and failure boundaries

Reuse the canonical path/ownership checks. Reject missing, linked, escaping,
foreign-contract or hash-mismatched artifacts, duplicate/unknown fields,
recursive references, invalid record counts and unsupported versions. Validate
the whole declared set with bounded resource use; never skip a malformed or
oversized member and report complete history.

Pin the original index/artifact plan across validation and publication. Refuse a
changed index or chunk rather than publishing a mixed snapshot. Preserve the
existing no-network validation rule and explicit preparation boundary. Do not
broaden fetch URLs or make a sidecar-supplied path/URL executable authority.

Historical capture/source provenance remains historical. Source review,
capture reproduction, release and running adoption stay distinct. Sidecars
cannot qualify an unknown runtime target or settle outstanding SDK/native work.

## Implementation and validation gates

- [ ] Land this contract through normal review before source implementation.
- [ ] Enumerate actual readers/writers and old-format behavior; wire the complete
      set without silently omitting an unknown consumer.
- [ ] Implement the versioned representation and lossless publication through
      the existing owners; preserve every record and existing safety check.
- [ ] Prove exact materialized-record equality and order before/after conversion
      and reconciliation of both real histories, with every artifact under the
      unchanged guard.
- [ ] Exercise missing/corrupt/foreign/linked chunks, count/version/field errors,
      concurrent input changes and interrupted publication without data loss or
      partial-history success.
- [ ] Exercise prior inline input, every canonical capture generation, source
      snapshot/closure, preparation and replay through the actual updated entry
      points. Preserve unrelated historical packets and capability floors.
- [ ] Run the canonical checker and genuine committed-source capture/no-write
      reproduction in the bounded development lane. Preserve unit-only,
      impact-scoped required CI; do not add native/process matrices to it.
- [ ] Review and merge the implementation, then return to the same #8389 source
      PR for reconciliation, fresh qualification and actual current-head review.

No source, native or operational completion is claimed by this proposal.
The full effort's Plan and Validation Plan remain the parent completion gate.
