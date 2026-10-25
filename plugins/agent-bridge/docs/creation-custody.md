# Internal original keyed-creation custody

Back to the [DB custody foundation](runtime-custody-foundation.md) and
[Bridge restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is an internal DB foundation, not an allocation/native effect grant,
automatic producer enrollment, public capability or restore qualification.

## Original attempts

Schema 36 adds nullable original-admission and input documents to the existing
`workspace_embodiments` and `provider_embodiments` rows. Their original key and
facts digests, session IDs, states, allocation and native observations remain in
those same tables. No second operation ledger is introduced. Writable migration
preserves legacy rows with null custody rather than inventing original inputs.
Already-current corruption refuses; passive opening and existing-only writable
adoption validate without schema repair, migration or writer startup.

An internal owner can capture a `CreationRequest` from its original kind, key
digest and creation facts, then call `CreationJournal.claim` with the observed
installation and enrolled frontend admission. The full original target,
options and workspace prompt are retained with the original session ID in one
short `synchronous=FULL` transaction. Facts use the existing creation digest
format. Canonical documents reject duplicate fields and non-finite values.
No external call holds that transaction.

The insertion guard orders new creation admission against DB epoch closure,
including ordinary unassociated creation. Exact journal replay returns the
same original row after closure or a lost reply; it does not re-enroll an owner,
change an admission, create another session ID or grant an effect. Changed
inputs/producer/epoch refuse. Existing legacy attempts cannot acquire an
association retrospectively.

Original key, facts, association and session identity cannot be overwritten or
discarded, including by `INSERT OR REPLACE` or a replacement update of a legacy
row into an associated destination key when recursive triggers are off.
State changes participate in the
existing inventory revision. Inventory reads every row, including allocating,
preparing, launching, complete and terminal attempts; associated facts are
validated against their original digest and producer association. Native
outcomes remain unqualified regardless of row status.

## Ordinary-path boundary

The existing workspace/provider owners now read an original attempt before
insertion. Replay no longer attempts an `INSERT OR IGNORE` that a closed-epoch
insertion trigger would reject before SQLite checks uniqueness. Their existing
keyed allocation, native receipt and first-turn behavior remains unchanged for
unassociated attempts. An explicitly associated attempt instead refuses
`creation_custody_required` before ordinary allocation or native launch. It
cannot downgrade into an unfenced legacy path.

No route selects this journal or accepts a caller-supplied admission. No actual
allocation, provider execution, Host/native launch, bind, resume or first-turn
effect is qualified by these methods. The later frontend integration must
retain the actual owner and original effect handoffs, recheck the open epoch at
each final boundary, preserve ambiguous outcomes and reconcile through these
same rows. Ordinary legacy replay is not evidence of complete runtime closure.

## Cost and validation

Ordinary new creation adds one bounded primary-key read before its existing
insert/read pair; replay removes the redundant write. No new process, network
call, background loop or history scan is introduced. The selected DB claim
uses one FULL transaction and restores the connection's prior synchronous
policy on success/refusal. Full-row input decoding belongs only to explicit
journal reads and strict inventory, not every ordinary start.

Focused controls cover both creation kinds, original inputs and immutable
history, closure/replay, concurrent SQLite owners, process exit after a committed
claim, FULL durability on normal/adopted connections, migration from the frozen
legacy schema, damaged-layout refusal and real owner-path downgrade refusal.
These are isolated source/storage controls, not native performance budgets,
SDK acceptance, attributed consumption, platform qualification or running
adoption. The effort's full cohort and installer gates remain open.
