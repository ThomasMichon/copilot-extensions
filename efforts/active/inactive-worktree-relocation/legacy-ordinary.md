# Legacy ordinary identity compatibility

Part of [Inactive Worktree Relocation](README.md), under #10879.
This is an **agent-recommended proposal**, not implemented support or
authority to repair a live record, install a runtime or move a real checkout.

## Intent and observed boundary

The existing identity-preserving relocation intent includes older retained
ordinary checkouts. Current v1 admission requires a nonempty original
`creation_nonce`; an unversioned retained ledger may have no such field.
Requiring a newly generated value would make admission depend on invented
creation history rather than preserving the existing unit of agency.

Qualify an operation-time identity for a supported unversioned subject while
retaining its original identity and metadata absence. Do not claim that a
current observation proves an original birth date, that missing metadata
proves age, or that a new operation token is an original allocation receipt.
This closes the existing [relocation intent](../../../visions/plugins/agent-worktrees/README.md#identity-preserving-checkout-relocation);
it introduces no new identity service or repository-wide allowlist.

This is distinct from #12821's recovery of an original numeric nonce and
#14316's PR URL read binding. Preserve those owners and their separate gates.
Neither correction is evidence that an unversioned checkout can be moved.

## Bounded class

Start with the existing ordinary completed-clean, sessionless, unpaired
managed linked-checkout class. Landed source, actual inactivity, supported
filesystem/data semantics, current receiving authority and settled creator
responsibility remain required. This proposal does not add WIP, retained
sessions, pairs, opaque providers, anchors or new platform support.

| Retained evidence | Required treatment |
|---|---|
| Valid original nonce | Keep the existing qualified nonce-backed route and preserve that nonce |
| Present but invalid/corrupted nonce | Refuse or use its existing exact recovery owner; never fall back to a newly invented identity |
| No persisted nonce | Consider only the explicitly qualified unversioned-subject route, not automatic legacy or inactivity clearance |
| Original receiving-operation hint or receipt | Retain and reconcile through its owner; absent nonce cannot erase that evidence or imply an ordinary allocation |
| Missing, foreign, ambiguous or unsettled creator evidence | Hold for its existing ownership/recovery boundary; a missing parent file alone does not prove a retired owner |

Creation/receiving stores must still be inspected through their bounded owners
when the nonce is absent. The current nonce-dependent classifier's early return
cannot become proof that no original receiving operation exists.
Unreadable, busy, changing, redirected, malformed or unsupported evidence
remains a refusal, not an empty inventory.

## Identity and custody obligations

The implementation must define and qualify an exact operation-time witness
using the existing receiving ledger, project/execution namespace and reciprocal
Git membership/registration owners. Bind the actual retained subject and
incarnation, not only a path spelling, worktree suffix, timestamp, HEAD or
reporter name. Demonstrate why a reused ID, replaced ledger or different Git
administration cannot reuse the witness.

Preserve the stable worktree ID, original owner/claim/handoff representations,
branch/HEAD/index, content, PR associations and original nonce absence.
Allocation defaults and current reporter customization do not rewrite history.
Any new token identifies this relocation operation at its actual observation
time, not the original creation. Existing valid original receipts stay immutable.

Reuse participating relocation, launch, lifecycle, record, claim and receiving
owners. Pure inspection must not recover or initialize receipts, acquire
mutation/admission owners or issue live-owner queries implicitly.
Recheck current subject, content, creator responsibility and reservation
evidence before publication and source retirement. Generic launch/mutation
remains frozen for an unfinished move. No forced claim settlement, owner
adoption, server termination or metadata stamping is an admission shortcut.

## Observable delivery sequence

### 1. Review the compatibility boundary and reproduce the refusal

Complete this proposal's normal review gate before runtime implementation.
Use synthetic registered projects and actual retained linked Git checkouts
whose ledgers have no nonce field. Capture the current strict refusal through
the selected real parser/process consumer. Keep a nonce-backed control and
explicit corruption/receiving-evidence refusals.

### 2. Qualify one complete ordinary legacy flow

Expand the existing operation and selected-runtime discovery rather than
adding another allocator, migration daemon or effectful probe.
Qualify the operation-time witness, complete copy/Git repair/ledger rebinding,
destination lookup and supported mutation, then safe source retirement.
Prove that no creation nonce or historical receipt was fabricated.

Keep all actual files, including ignored content, within the unchanged
qualified filesystem/data boundaries. Use a changed synthetic allocation root
and differently named project reporters while retaining unrelated active
subjects. Failure to qualify a required owner or observation remains a hold.

### 3. Prove interruption, mixed readers and truthful adoption boundaries

Journal status/retry identifies the original move, its witness and unsettled
effects. Cover every existing recovery boundary, including Git repair before
the phase marker and before the destination record save. Preserve copies and
custody on changed evidence or lost responses; do not allocate a replacement
checkout, generate new creation metadata or replay unknown effects.

Follow [service contract evolution](../../../docs/patterns/service-contract-evolution.md):
declare support against the selected executable and participating writer before
callers use the new class. New callers must refuse an old provider without an
effectful fallback; old readers/writers must refuse unsupported retained
operation state before changing it. Preserve unrelated unmoved legacy entry,
lookup and finalization behavior.

Publish support/recovery documentation only for behavior actually qualified.
Normal review, required CI, accepted Main release, separately authorized
installation and actual adopter practice remain distinct gates. A fixture
or planning merge is not a real move or completion of #10879.

## Focused validation

- Failing-first absent-field control through the real CLI; nonce-backed
  behavior unchanged; numeric/invalid fields are not reclassified.
- Real cold receiving-ledger/Git membership and original claim witnesses,
  including different reporter names and changed allocation defaults.
- Foreign project/host/environment/Git family, ID reuse, replaced ledger or
  Git administration, changed branch/HEAD, stale witness, unknown/opaque
  ownership and handoff reservation refuse without changing evidence.
- Missing/corrupt original receiving metadata, matching creation-journal rows,
  ambiguous/busy/unsupported stores and changed snapshots stay held even
  without a nonce. No scan or time budget is widened to manufacture absence.
- Same-volume and actual Windows cross-volume complete consumers preserve
  bytes, metadata, original fields/absence and unrelated active subjects.
  Keep native/process cases explicitly selected, not an exhaustive required
  unit-CI matrix or an inference from mocked owner absence.
- Interrupt/retry/status, contention and lost-response controls prove one
  original operation across all stages, including both Git-repair windows.
  Revalidated responsibility can still stop retirement.
- Mixed supported/unsupported caller, provider, writer and retained-journal
  directions, plus rollback/refusal controls, preserve strict syntax and
  honest incompatibility rather than silently downgrading preservation.

The exact witness representation, journal evolution and recovery composition
are implementation decisions with the obligations above. Name and test their
owned contracts before effects; do not substitute a speculative sidecar,
unchecked timestamp or generated nonce for the missing proof.
