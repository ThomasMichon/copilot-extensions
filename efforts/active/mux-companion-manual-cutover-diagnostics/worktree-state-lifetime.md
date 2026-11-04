# Predictable Worktree Data, Discovery and Archival

Canonical parent: [complete effort](README.md).
Sidecar dependency: [S2 and its validation](sidecar-live-timeline.md).
Standing intent: [worktree data](../../../visions/plugins/agent-worktrees/worktree-data/README.md).

**Status: design reviewed in #10199; D1 placement/discovery source merged in
#10224; D2 managed initialization/export source merged in #10238; D3 Manager
consumer source merged in #10251; D4's explicit collection/readback library
merged in #10447; passive project retained-inventory source merged in #10488;
Efforts export-contributor provenance merged in #10495; Logger's optional
provenance collection/readback consumer merged in #10500.**
Native durability and complete D4-D5 remain open.
No source merge performs a live data move,
export, collection, cleanup or rollout. The
[D1 readback](journal/2026/11.03-sidecar-durable-placement.md) and
[D2 readback](journal/2026/11.03-sidecar-managed-agenda-export.md) record exact
source acceptance.

## Request - verbatim

Earlier storage clarification:

> Presently, we're storing the agenda state in a hidden folder in the checked-out worktree. There is a risk that worktree-cleanup flows will simply delete all that data, when we will want the agenda and its history durably persisted, likely to be included with agent-logger's session archival system. We may want to store agenda data in the `.<repo>/worktrees` data folders, and augment agent-logger's session-sync to start archiving worktree-state folders, ensuring we rescue them before cleanup, or stop deleting them. We'll want to do a little design/architecture on this concept.

Subsequent discovery clarification:

> We need somewhere that the Manager can predictably find, and subsequent logging and history can predictably find and archive

The required outcome is shared predictable discovery and durable history, not
an invocation flag that each later consumer must remember. The physical layout
and protocols below are **agent-recommended**, not quoted operator selections.

## Current boundary

The adopter chooses and creates an agenda state home; initialization creates
the store there. The committed publisher knows that home transiently but sends
only the coarse observation. Its source identity is exactly worktree, session
and store ID. The resident retains the observation, not the agenda directory.

The rich reader can inspect an explicitly selected home and expected store.
It cannot derive a path from the store ID. Store format 2 and reader contract 1
are separate versions. The coarse publication alone does not discover a rich home.

#10224 adds the worktree owner's bounded descriptor and exact-store passive
discovery outside the checkout. The owning
[worktree-data contract](../../../plugins/agent-worktrees/docs/worktree-data.md)
records allocation and filesystem refusal semantics. #10238 composes explicit
new managed initialization with complete consistent context-qualified export
and local readback. #10251 connects Manager to that discovery and the owning rich reader,
using the existing single permanent Agenda subscription. #10447 supplies
Logger's explicit local collection/readback library. #10488 supplies passive
project-wide retained inventory at the Worktrees owner. #10495 supplies
Efforts-owned complete contributor provenance from the same verified export cut.
#10500 preserves that complete projection in Logger's explicit local
collection/readback library without coercing canonical session references.
Automatic consumer discovery, existing-tick orchestration and legacy rescue
remain unfinished integrations.

Existing cleanup can remove a checkout while retaining a lifecycle tombstone.
That tombstone does not preserve arbitrary checkout-local agenda bytes.
Session Sync's current session-shaped collections are not a complete archive
contract for worktree state. Current read/history pages and coarse projections
are not lossless exports.

## Proposed ownership and placement - agent-recommended

| Responsibility | Existing owner and boundary |
| --- | --- |
| Repository/worktree identity, data-home allocation and locator lifecycle | agent-worktrees, using its installation-aware project-data resolution |
| Agenda format, canonical transactions and consistent complete exports | efforts; no Manager or logger writes to its ledger |
| Passive presentation and history reads | Manager, through actually negotiated owner contracts |
| Collection, archival and readback | agent-logger, through existing repository/destination authorization |
| Checkout disposal and persistent-data reclamation | Existing lifecycle owner, as separate explicitly gated operations |

Use the existing project-data root, outside the disposable checkout. A possible
layout is:

```text
<project-data>/
  worktrees/<full-worktree-id>.yaml
  worktree-state/<full-worktree-id>/agendas/<lane>/agenda.sqlite3
```

This is an illustrative namespace, not a new hardcoded global root. Resolve the
actual installation/project owner before allocation. The agency record keeps a
bounded descriptor, not the agenda ledger. Live SQLite stays on a suitable local
filesystem; archive transport consumes consistent exports, not a cloud-synced
live database.

One owner-published descriptor identifies repository/worktree incarnation,
domain/lane, store identity and current placement generation. A path is a
machine-local resolution result, not portable identity. Manager, subsequent
sessions and logger use the same read boundary; none maintains its own path map.
The descriptor remains discoverable after checkout retirement. An unavailable,
ambiguous or stale placement fails visibly instead of triggering a filesystem
search, new store or replacement runtime.

Keep the exact current agenda-v1/v2 wire fields unchanged. Introduce a separately
negotiated owner discovery/read capability before changing callers. Discovery
does not allocate, migrate, ensure a host or import an independently installed
sibling runtime. Match the discovered store and exact worktree against both the
owning reader and selected coarse publication before consuming rich history.

Standalone efforts remains usable with explicit state homes. Managed retention
guarantees apply only to actually admitted placement; an arbitrary unregistered
directory is not silently claimed as managed data.

## Consistent export and collection - agent-recommended

Efforts supplies a complete consistent export of its declared agenda data:
current state, accepted-operation ledger, immutable records and preserved
context/evidence, with schema, store identity, revision and member hashes.
Concurrent mutations cannot mix revisions. Export/readback is read-only and
does not replay operations or silently initialize a missing store.

Logger treats this as a typed worktree-state collection alongside sessions.
Deduplicate by worktree/store/revision and retain references from contributing
sessions; do not copy the same agenda beneath each session or assign it to the
last session that happened to touch it. Existing source-claim compilation,
privacy, database-publication and destination policies still govern effects.

Credentials, live sockets, leases and other authority-bearing runtime material
are outside the declared collection. If accepted agenda content cannot be
archived under the selected privacy policy, report that block; do not silently
redact it and call the result lossless. Rendered prose, loaded history pages and
successful local copies are not substitutes for the owner export.

An archive receipt binds the actual admitted destination, store identity,
revision and verified manifest/member hashes. Local transfer completion is not
durable remote acknowledgement. Later logging and history resolve retained or
archived evidence through its owner and receipt rather than guessed paths.

## Lifecycle and transition - agent-recommended

1. **New managed data:** explicitly allocate/admit a data home through the
   worktree owner, then initialize the agenda there. Do not silently fall back
   into the checkout while claiming managed retention.
2. **Existing checkout-local data:** identify the real store and writer,
   quiesce through that owner's procedure when authorized, create and verify a
   consistent rescue, preserve store identity/history, and switch the locator
   once. No two writable copies and no fresh initialization masquerading as
   migration. If preservation is not established, retain the containing
   checkout rather than deleting the data.
3. **Checkout retirement:** preserve all current effort, source and resource
   gates. Once admitted data is safely independent, checkout disposal can retain
   its data descriptor and tombstone without requiring an active worktree or a
   presently reachable archive destination.
4. **Persistent-data reclamation:** a separate operation requiring applicable
   retention policy, verified revision-bound archival and explicit deletion
   authority. Ordinary cleanup and a successful sync never authorize it.
5. **Restoration:** restore verified historical data without resurrecting head,
   claims, leases or execution authority. Re-admit actual placement separately;
   format version is not a restore epoch. When an independent continuity or
   last-known-revision witness is absent, expose uncertainty instead of claiming
   that a copied store could not have rewound.

Known legacy fallback locations require preservation/refusal coverage. This is
not a promise to discover every arbitrary unregistered directory. Migration,
collection, remote publication and deletion remain effect-specific approvals,
not consequences of a reviewed design.

## Plan

- [x] D0. Review this amendment and standing intent through the normal PR flow
  before any allocator, discovery, export or cleanup integration executes.
  #10199 merged as `a9645aa98b9a4217274cc61aa36a073806413a34`; at that merge
  D1-D5 and the complete Validation Plan remained unchecked.
- [x] D1. Extend the existing worktree owner with admitted data allocation,
  bounded persistent descriptors and passive exact-identity discovery. Cover
  fresh managed homes and read-only unsupported/unknown behavior; no live move.
  #10224 merged as `3da6f1c3e534fd2df0b65775ee17c6d252ec9c42`; this is
  source delivery with the native durability acceptance below still open.
- [x] D2. Have efforts consume managed placement for explicitly selected new
  stores and provide consistent complete export. Preserve standalone/legacy
  operation; do not automatically migrate an active lane.
  #10238 merged as `ca938fd8f998933624accae2ce8371001f58f24c`; this is
  source and isolated process delivery, not installed/native or archive proof.
- [x] D3. Connect Manager to that discovery and existing push revisions, then
  the owning rich reader, retaining one permanent Agenda subscription.
  #10251 merged as `8b01db9aff9064434c839d2275eada0d27f876da`; source and
  isolated real-process/Textual qualification only, not installed/native proof.
- [ ] D4. Extend the existing logger collection and readback flow for exported
  worktree state under its actual repository/privacy/destination policy. Retain
  the existing Session Sync/accounting owners; no parallel scheduler or service.
  #10447 supplies the policy-qualified local library and namespace/preservation
  fencing. #10488 supplies negotiated owner project inventory, including
  descriptors without canonical records. #10495 supplies complete owner-derived
  contributor provenance; #10500 supplies its explicit Logger library consumer.
  Actual selected-provider discovery and existing-tick orchestration remain open.
  See the [consumer readback](journal/2026/11.04-sidecar-logger-contributors.md).
- [ ] D5. Qualify legacy rescue and the separation of checkout removal from data
  reclamation. Actual relocation, collection, deletion and rollout need their
  own concrete authorization and evidence.

## Validation Plan

- [x] Qualify explicit local Logger consumption of complete optional contributor
  provenance, preserving nullable/noncanonical identities and original v1 calls,
  with revision-bound metadata and fresh owner readback. #10500 qualifies the
  isolated library/process boundary, not automatic discovery or actual archival.
- [x] Qualify provider-only export-contributor provenance from the same verified
  read-only snapshot, retaining nullable/noncanonical caller identities, exact
  raw manifest/revision binding and whole-result count/byte/time refusal.
  #10495 preserves the original export/verify contracts and existing Logger
  client's process behavior; it does not qualify consumer attribution or collection.
- [x] Qualify provider-only project inventory across retained descriptors,
  missing canonical records, actual repository/cell identity, finite bounds and
  no-write/error behavior. #10488 supplies isolated source/process evidence,
  not automatic consumer discovery, collection or native Windows qualification.
- [x] The actual allocation owner and independently invoked readers resolve the
  same store without a checkout, session, consumer-local map or manual path flag.
  Qualified in isolated real CLI processes in #10224, not installed adoption.
- [x] Exact project/worktree incarnation, lane, store and placement fences reject
  traversal, aliasing, stale/mismatched bindings and wrong installation cells.
  Unknown/old providers do not bootstrap or fabricate an empty complete store.
  #10224 qualifies the source owner and passive Bash/PowerShell-on-Linux paths.
- [ ] Atomic allocation/registration and interrupted relocation leave at most
  one admitted writer location; conflicting or partial registration is visible.
  #10224 qualifies new-home publication/faults and concurrent worktree allocation;
  no active-store relocation or native Windows durability is qualified.
- [ ] Execute the owning allocation/retry and complete-export durability controls on native Windows
  and record actual filesystem barriers separately from Linux API-double
  coverage. Hardware power-loss behavior and unsupported filesystems are not
  inferred from source tests.
- [x] Qualify efforts-owned new managed initialization and complete local export/
  readback in isolated processes, preserving declared identity and every
  accepted operation/record under concurrent mutation. #10238 covers malformed
  stored requests, integrity/completeness faults and exact retries. This is not
  remote archival acknowledgment, restoration or native Windows acceptance.
- [x] A real producer-to-CLI-to-Manager join follows committed agenda revisions,
  pages backward while browsing and cancels all owned reads; unrelated views and
  the existing coarse subscription remain usable.
  #10251 qualifies the actual isolated owner/publisher/resident/CLI/reader/widget
  chain and retained-source lifecycle controls. Native and archival gates remain.
- [ ] Owner export and archive readback preserve the complete declared ledger,
  immutable records, original identity and revision under concurrent mutation.
  Omitted/truncated/rewritten members fail the hash/completeness oracle.
  #10238 delivers the owner export/local-readback source portion; owner discovery,
  scheduled orchestration, destination acknowledgment and actual archive readback
  remain open.
  #10447 qualifies explicit local Logger collection and fresh owner readback in
  isolated processes, not automatic discovery or actual archive acknowledgment.
- [ ] Multiple contributing sessions reference one revision-bound worktree
  collection. Existing logger aggregate authorization, privacy, idempotency
  and destination ownership remain effective.
  #10447 qualifies two explicitly supplied references to one local collection;
  #10495 supplies the separately negotiated owner provenance projection.
  #10500 qualifies explicit complete attribution/readback alongside unchanged
  canonical reference calls. Automatic consumer discovery/reference integration
  remains open.
- [ ] Checkout retirement retains managed data and its discoverable descriptor;
  legacy or unverified rescue prevents data-destroying removal. Archive outage
  alone neither erases data nor fabricates an unfinished source objective.
- [ ] State reclamation refuses absent/stale archive receipts, changed revisions,
  missing retention decisions and absent deletion approval. Restoration never
  reacquires a claim, lease or head from archived bytes.
- [ ] Record source, isolated fixture, installed/native and actual archive tiers
  separately. A proposal, source merge or local copy closes none of the later
  operational gates.

## Completion boundary

The parent remains Active. This design adds no live data-home change, migration,
collection, pruning or rollout. Independent already-authorized source units can
continue while this producer-first prerequisite is reviewed. Any later transfer
of uncompleted archival or retention acceptance must name a tracked objective
and owner rather than disappearing behind a completed Manager adapter.
