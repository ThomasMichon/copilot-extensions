# Internal original keyed-creation custody

Back to the [DB custody foundation](runtime-custody-foundation.md) and
[Bridge restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is an internal DB and explicitly selected local creation owner, not automatic
producer enrollment, a public capability or restore qualification.

## Original native observations

Schema 37 additionally prepares nullable native handoff, launch start/result and
binding start/acceptance fields on the same `workspace_embodiments` row. It
does not add a second acquisition attempt or operation ledger. Legacy rows and
provider rows do not acquire native custody during migration.

Schema 38 separately prepares nullable native handoff, launch start and result
fields on `provider_embodiments`. `CreationJournal` uses the same prospective
`prepare_launch`, one-shot `begin_launch`, `read_launch` and immutable
`report_launch` methods, with a provider-specific handoff containing the full
original provider target, admission, session and consumer incarnation. The
unchanged full target/options document and its digest remain on that same row;
the legacy placement projection is not used to shorten original custody.
Migration does not associate legacy rows or infer a handoff from an existing
native identity.

Provider acceptance moves the original row to `complete`, meaning only native
identity acceptance in that creation protocol. It does not imply a first prompt,
consumption, semantic completion or qualified remote custody. Provider rows have
no workspace allocation, binding or turn columns; those journal operations
refuse provider requests. An unknown launch remains outstanding across closure
and process exit; late acceptance may be retained without another launch grant.
Strict inventory reads these fields even on terminal rows, while passive opening
and writable adoption require the exact prepared columns and guards without
repairing current-schema corruption.

This is an internal DB prerequisite only. No provider subprocess, transport,
Host or native effect selects these methods yet, and ordinary associated
provider creation still refuses before launch. Actual provider effect-owner
mediation and remote custody qualification remain separate gates.

For workspaces, `CreationJournal.prepare_launch` retains the original allocation, key and facts
digests, session, admission and consumer incarnation before launch preparation.
An exact retained preparation can be observed after closure; another consumer
cannot replace it. Separate `begin_launch` and `begin_binding` operations each
require the original open epoch in a short FULL transaction and admit their
original boundary only once. They do not execute an effect. A lost reply remains
an outstanding launch or binding, never permission to retry.

`report_launch` retains the exact accepted native identity and
`report_binding` retains the original binding observation after closure.
These observations remain distinct from first-turn admission, native
consumption and semantic completion. Recorded handoffs, times, native identity
and allocation cannot be cleared or replaced. The first prompt's zero turn index
is also retained. Strict inventory validates them
against the full original row and refuses inconsistent state even in a terminal
row. Passive and migration-free adopted connections require the complete
prepared schema and guards without repairing corruption.

The DB methods do not execute effects. The separately selected internal
`CreationFrontend.start` carries a concrete `CreationLaunchOwner` through the
shared local Host/ACP owners below. `allocate` remains allocation-only, and
ordinary associated creation remains refused.

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

No route selects this journal or accepts a caller-supplied admission. Provider,
remote and resume effects remain unsupported for associated creation. Ordinary
legacy replay is not evidence of complete runtime closure.

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
later in an otherwise full-start request. Complete cross-owner allocation
qualification remains a separate validation gate.

## Selected original local native creation

`CreationFrontend.start` supports only a new local project workspace and its
serializable launch options; unsupported target/options refuse before allocation.
It retains the full request, including the prompt and unshortened owner reference,
through the same allocation method. The existing keyed executor spans launch,
binding and first-turn admission; the existing workspace lock and shared launch
gate exclude competing starts without fabricating an acquisition request.
Serialized environment overrides are merged into a fresh copy of the retained
target before local preparation, preserving unrelated base environment keys
and the unchanged original target/options facts.

Before launch preparation, `CreationLaunchOwner` retains its original same-row
handoff and session. It reuses `LocalLaunchOwner`'s actual Session/target/Host/
child/ACP reference and birth checks, final `Popen` admission, one-shot native
new invocation and per-effect configuration fences. The local launcher accepts
only the concrete acquisition or creation adapters, not arbitrary implementations.
The shared local child reader verifies the original frontend parent, process,
DB file, producer and open epoch; the creation reader then checks its own
key/facts/allocation and outstanding handoff. Attested exec repeats this check.
Neither the original prompt nor the Host-only context is inherited by the child
through its environment.

After exact native acceptance and configuration, binding uses the existing
`bind-session` owner with a one-shot FULL fence at its actual subprocess
boundary. Selected original launches skip the ordinary best-effort lineage
registration in `start_session`; their actual binding owner must perform that
ground write, not mutate the workspace before its own final admission.
Unselected local starts retain ordinary lineage registration.
An independent exact-workspace head read proves the binding result
before acceptance is retained, even when the epoch closed while the reply
returned. An unknown native or binding reply remains held without retry.
The accepted original Host transport is captured only after proven binding.
It carries the exact retained first prompt and zero turn through the existing
original-turn protocol, including the final SDK gate; no unassociated prompt
or second delivery ledger is used.

An exact completed replay returns only the retained local session/result,
including after closure, without allocation, launch, binding, prompt or revision
change. A missing local session or any partial/unknown native outcome refuses
rather than recreating, resuming, reattaching or re-enrolling it. Durable session
association preserves the existing automatic-recovery, resync, lifecycle-handoff,
direct ACP and prompt exclusions before transport capture and after reload.
Ordinary Host status and graceful detach never mark an associated or retained
native transport reapable merely because its current turn is idle; retiring
that Host requires its actual custody owner. Unassociated idle Hosts retain
their ordinary reaping behavior.
The existing idle-TTL sweep uses the same custody predicate under the session's
turn lock. It skips a lifecycle-busy candidate rather than waiting under that lock;
reattachment may hold that lifecycle lock while submitting its resume prompt.
Its internal automatic-stop path rechecks the original live session after
acquiring the lifecycle lock and at the final Host-retirement boundary;
late enrollment cannot turn a stale idle snapshot into reaping permission.
Explicit operator stop/reap behavior is unchanged.
The actual new local Host launch also sets its existing unexpected-disconnect
and active-reap timeout arguments to zero for the concrete original owner.
A graceful `detach(False)` must not arm a quiet-active timeout that later
retires the retained child. Ordinary unselected Hosts retain their configured
timeouts; existing Hosts are not retrospectively adopted or reconfigured.
The shared [local startup custody owner](acquisition-custody.md#explicit-local-effect-owners)
also retains actual process/transport observations before awaited startup.
Unknown readiness, initialize/new replies, cancellation and closed-epoch
configuration failure leave that original Host/child held, not physically
retired by ordinary rollback.
Native acceptance, first-turn admission, attributed consumption and semantic
completion remain separate.

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

Selected full creation reuses the existing Host/child and binding subprocesses.
It adds bounded same-row and live-identity checks and short FULL transitions at
their final boundaries, plus the exact-workspace binding readback. Ordinary
unselected launches do not acquire passive DB opens or process-birth checks.
No new daemon, background scan, provider preflight or synchronous telemetry
export is introduced. Actual native timing and platform budgets remain unmeasured.
Idle Host-reapability checks without a captured native owner add one existing
session primary-key association read; busy and captured-owner paths short-circuit.
The existing expired-candidate idle sweep repeats these bounded checks at its
lock and final-retirement boundaries, without a new timer, history scan or process.

Provider DB custody adds only bounded same-row reads and short FULL commits on
explicit journal calls. Ordinary provider execution gains no preflight,
background work or automatic enrollment. SQLite process-exit and concurrent-owner
controls qualify storage behavior only; no provider or native timing budget is
claimed.

Focused controls cover both creation kinds, original inputs and immutable
history, closure/replay, concurrent SQLite owners, process exit after a committed
claim, FULL durability on normal/adopted connections, migration from the frozen
legacy schema, damaged-layout refusal and real owner-path downgrade refusal.
The allocation controls additionally cover the actual subprocess callback with
a synthetic owner, executor exclusion across an await, closure during resolver
preparation, closure after final admission, unknown replies, immutable outcomes
and observation through a fresh DB connection. These are isolated source/storage
and synthetic-process controls. The selected native controls use the actual
local Host, separate synthetic ACP child and SDK, including original-row
readback, preference application, unknown native/binding replies, closure
during binding/capture/first-prompt admission, and no ordinary lifecycle downgrade.
Selected Host arguments are checked with a nonzero configured active timeout;
startup controls additionally keep live peers silent without exiting, exercise
initialization and native timeouts, cancel a pending native call, and let the
actual Host become ready after its readiness deadline. Child environment
readback verifies retained overrides, not only forwarded manager arguments.
actual graceful-detach controls cover both the production-default bound and a
short nonzero bound, waiting beyond the latter while the same Host stays alive.
They are not native performance budgets,
SDK acceptance, attributed consumption, platform qualification or running
adoption. The effort's full cohort and installer gates remain open.
