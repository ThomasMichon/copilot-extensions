# Internal original keyed-creation custody

Back to the [DB custody foundation](runtime-custody-foundation.md) and
[Bridge restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is an internal DB and explicit local allocation foundation, not a native effect grant,
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

No route selects this journal or accepts a caller-supplied admission. Provider
execution, Host/native launch, bind, resume and first-turn effects remain
unsupported for associated creation. Ordinary legacy replay is not evidence of
complete runtime closure.

## Explicit local allocation owner

An internal `CreationFrontend.capture` binds the actual manager, DB object and
file identity to its already-enrolled frontend process and birth. It does not
enroll or reopen anything. Its separately selected `allocate` method retains the
complete original target, prompt and options, including the full owner reference,
before awaiting policy or owner preparation. It uses the existing keyed creation
executor and the same `create --creation-key --no-owner --interface acp` command
as ordinary workspace creation. Existing-workspace, remote, elevated and provider
targets refuse before a new attempt.

Immediately before the existing `worktree_command` subprocess spawn, the same
owner rechecks manager, process, DB file, target and open epoch. A short FULL
transaction moves that original row from `allocating` to `allocation_started`;
it can happen only once. No transaction spans process execution. The window
after final admission is outstanding allocation custody, not evidence that
the external allocation completed or did not happen.

An exact successful keyed allocation response records its project, path,
worktree ID and nonce on the same row as `allocated`, including after closure.
A replay by the original captured frontend validates its identity and retained
producer history without requiring an open epoch, even during draining. It
returns that observation without another subprocess or inventory revision.
Fresh allocation still requires open admission before policy preparation and
again at the final subprocess boundary. Lost, malformed,
cancelled or wrong-project replies retain `allocation_started` without retry
permission; a restarted reader can observe the same attempt. A changed result
cannot replace the retained identity. The ground owner's own keyed allocation
protocol remains unchanged; this slice does not claim to enroll that process,
negotiate a child epoch or qualify its internal filesystem effects.

`allocate` returns an allocation observation, never a native session or a
complete embodiment receipt. It does not call `start_session`, bind a native
head, resume or submit a prompt. Full `workspace_creation.start` continues to
refuse associated attempts before ordinary effects. This separate internal
entry prevents allocating only to discover an unsupported native boundary
later in an otherwise full-start request. Native integration and complete
cross-owner allocation qualification remain later source/validation gates.

## Cost and validation

Ordinary new creation adds one bounded primary-key read before its existing
insert/read pair; replay removes the redundant write. No new process, network
call, background loop or history scan is introduced. The selected DB claim
uses one FULL transaction and restores the connection's prior synchronous
policy on success/refusal. Full-row input decoding belongs only to explicit
journal reads and strict inventory, not every ordinary start.

Selected allocation adds bounded process/DB identity reads and FULL transitions
to its existing one-subprocess command. Replay adds no subprocess or network
work. The ordinary path gains only a shared Python helper call with its command
and response checks unchanged. The acquisition frontend reuses the same process
validator without adding an owner, process or background scan.

Focused controls cover both creation kinds, original inputs and immutable
history, closure/replay, concurrent SQLite owners, process exit after a committed
claim, FULL durability on normal/adopted connections, migration from the frozen
legacy schema, damaged-layout refusal and real owner-path downgrade refusal.
The allocation controls additionally cover the actual subprocess callback with
a synthetic owner, executor exclusion across an await, closure during resolver
preparation, closure after final admission, unknown replies, immutable outcomes
and observation through a fresh DB connection. These are isolated source/storage
and synthetic-process controls, not native performance budgets,
SDK acceptance, attributed consumption, platform qualification or running
adoption. The effort's full cohort and installer gates remain open.
