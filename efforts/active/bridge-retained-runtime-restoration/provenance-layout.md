# Lossless contract provenance layout

Back to the [effort](README.md). **Prospective, agent-recommended design.**
Source-only format work is authorized and this contract cleared normal review
in #8420. Its implementation and dependent-source evidence gates are complete
through #8469, #8503 and #8389. It does not authorize runtime changes or close
the full restoration effort.

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

- [x] Land this contract through normal review before source implementation.
- [x] Enumerate actual readers/writers and old-format behavior; wire the complete
      set without silently omitting an unknown consumer.
- [x] Implement the versioned representation and lossless publication through
      the existing owners; preserve every record and existing safety check.
- [x] Prove exact materialized-record equality and order before/after conversion
      and reconciliation of both real histories, with every artifact under the
      unchanged guard.
- [x] Exercise missing/corrupt/foreign/linked chunks, count/version/field errors,
      concurrent input changes and interrupted publication without data loss or
      partial-history success.
- [x] Exercise prior inline input, every canonical capture generation, source
      snapshot/closure, preparation and replay through the actual updated entry
      points. Preserve unrelated historical packets and capability floors.
- [x] Run the canonical checker and genuine committed-source capture/no-write
      reproduction in the bounded development lane. Preserve unit-only,
      impact-scoped required CI; do not add native/process matrices to it.
- [x] Review and merge the implementation through #8469.
- [x] Review and merge the historical HTTP-46 qualification helpers through #8503.
- [x] Return to the same #8389 source PR for reconciliation, fresh qualification
      and actual current-head review after the remaining capture-owner gate.

No source, native or operational completion is claimed by this proposal.
The full effort's Plan and Validation Plan remain the parent completion gate.

## Implementation evidence in progress

The current source inventory uses one codec in
`tools/agent_bridge_contract_registry.py`; semantic validation remains in the
existing checker. The updated entry points are:

| Surface | Reader/writer binding |
|---------|-----------------------|
| Checker | Complete materialization before existing source/capture checks |
| HTTP-45 and HTTP-46 captures | Shared snapshot/plan and explicit publication owner |
| HTTP-47 capture | Committed metadata snapshot, complete ordered history, closure inputs and the same publisher |
| Preference-authority capture | Committed complete history and declared three-vector output ownership |
| HTTP-47 closure and CI preparation | Read every captured metadata artifact from the exact source commit |
| Fixture maintenance and replay | Copy/pin sidecars with their index; inspect complete provenance |

The prior checker explicitly supported only schema version 1. The prior
HTTP-45/46 writers read inline provenance directly and could update an existing
inline record; they did not materialize or protect an archived prefix. They are
not compatible writers for version 2. Updated readers retain version-1 input;
use the updated capture owners for version-2 publication.

Bounded controls now cover legacy inputs, lossless chunk materialization,
immutable-prefix refusal, interrupted same-plan recovery, input mutation before
the final index commit, complete captured metadata preparation and the canonical
checker over more than 1 MiB of materialized provenance. Capture guard tests
retain their actual alias, closure and portfolio checks rather than accepting a
size-limit refusal as a proxy for the intended failure.

At the initial implementation checkpoint, an in-memory three-way check used the
genuine shared ancestor and verified that
both actual source histories preserve its complete prefix. Retaining that
prefix once and both ordered append-only deltas produces 1,856 records and
1,062,639 inline bytes. The lossless plan has five chunks, a 228,258-byte index
and a largest artifact of 261,986 bytes, with exact materialized equality and no
writes. This is conversion evidence, not a published registry, current-source
capture reproduction, implementation review or runtime qualification.

The committed implementation also completed a genuine HTTP-47 capture and its
no-write reproduction through the existing bounded owner: all 117 selected
model-free ASGI/owned-process cases passed on both runs, with the complete
interpreter/entrypoint receipts checked before publication. The canonical
checker accepts both contracts and all 583 fixtures. This refresh retains
version 1 while its final 1,037,891-byte, 1,786-record index fits; conversion is
exercised by the lossless three-way and canonical-checker controls, not claimed from that
ordinary inline capture. The final write/no-write pair binds the provider-visible
source checkpoint `10206a19b416298785dc7a39ffc96cecdf7c2abe`, retained through
the normal managed source push before capture. Additional actual HTTP-45 and
preference-authority entry-point controls consume both inline and chunked
history, retaining their real admission/refusal and complete-history assertions.
Final integration review and the remaining capture-owner closure remain open.

Each already-published output must equal its planned bytes before registry
replacement. Journal files stage completely before atomic publication.
Explicit capture writes recover only an exact journal-owned interrupted
hardlink pair under the kernel lock; unrelated aliases still refuse.
Orphaned-checkpoint closure binds the captured metadata prefix rather than
later publication chunks, and committed metadata uses explicit UTF-8 decoding.
Focused controls cover reverted/removed outputs,
partial old/new/manifest staging, owned versus foreign aliases, size-triggered
conversion with unavailable source checkpoints, and real non-ASCII Git blobs
under a simulated legacy host code page.

The repaired source checkpoint `5820a7fb022e869dbd192044561c44bbcab26144`
was retained through the managed source push before another genuine capture.
Both write and no-write runs passed all 117 required model-free cases and
validated interpreter/entrypoint receipts. This time the canonical publisher
actually converted inline history to version 2: 73 declared outputs, five
chunks, a 228,258-byte index and a largest chunk of 261,917 bytes. All 1,786
prior records remain exact ordered prefixes of the complete 1,853-record
history, with every artifact below the original cap. The canonical checker
again accepts both contracts and all 583 fixtures. The implementation cleared
current-head review and merged in #8469. The remaining historical capture-owner
gate and dependent-source qualification are still required; this source evidence
does not qualify a running receiver.

The remaining HTTP-46 capture-owner control now has a positive isolated Linux
run against locally retained HTTP-46 source
`c0b0dbba0f639344c5e4b384ccffe7b7ede1cc02`. The complete historical runtime and
library cohort is preserved; only current metadata owners and qualification
tests are overlaid. Both inline and actual chunked inputs completed the real
write/no-write entrypoints and their contained startup replay. Existing chunks,
ordered history, foreign/pre-46 packets, floors and the unchanged size guard
were retained. Replay imports were checked against the historical source
hashes. The source IDs in the receipt are genuine composed fixture commits,
not a reproduction of the unavailable original capture checkpoint.

The focused launcher is documented in the
[contract evidence guide](../../../plugins/agent-bridge/contract/README.md#qualifying-the-historical-capture-owner).
Qualification helper review/landing completed through #8503. The dependent
#8389 integration now retains the genuine shared ancestor once, all 269
upstream HTTP provenance additions and all 204 inbox-branch additions in their
original order: 2,006 HTTP and 51 preference records. The five published chunks
remain byte-identical; the index is 300,305 bytes, below the unchanged cap.
Only current-generation capture metadata conflicts selected the actual reviewed
upstream packet hashes; operation bodies are unchanged and both historical
capture lineages remain retained.

The complete merged inbox source
`c0b3a2a09a67d93c7ceb8d4cc936254ba2444beb` was retained through managed
publication, then completed the genuine HTTP-47 write and same-source no-write
entrypoints. Both passed all 117 required cases with complete
interpreter/entrypoint receipts and 68 outputs. The capture appends 67 HTTP
records, preserving every prior record in order: 2,073 HTTP plus 51 preference
records, five unchanged chunks and a 323,955-byte index. The canonical checker
accepts both contracts and all 583 fixtures against the reviewed base.
The final evidence head `fd4b237e7ef09b37d4c15ca64e103a44f66ce0af` received
Copilot approval; all findings were resolved and required checks passed.
Normal managed completion merged #8389 as
`d9363f0fd16539ebbd10fdf6a4ad724be4629023`. This closes this bounded
source-evidence prerequisite, not native, release, installed or serving-route
qualification.
