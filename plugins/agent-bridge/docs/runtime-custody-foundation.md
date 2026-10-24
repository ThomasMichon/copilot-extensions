# Internal DB custody foundation

Back to the [Bridge restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is an internal source foundation, not a supported restoration command,
complete runtime closure, native attestation or deployment authorization.

## Existing owners and covered effects

`Database` owns an additive installation-bound epoch and frontend-producer
observations in its existing SQLite database. Initialization is explicit; normal
schema bootstrap does not bind an installation, enroll a producer or close
admission. Existing rows remain unassociated history rather than receiving
fabricated producer identity.

The owner-facing methods are `initialize_runtime_epoch`, `runtime_epoch`,
`enroll_runtime_producer`, `close_runtime_epoch` and
`runtime_custody_inventory`. Their installation and PID/birth/incarnation values
are exact observations supplied by the owning caller. These methods neither
authenticate that caller nor resolve provenance or acquire native custody.
No public route or ordinary application startup calls them automatically.

`LoopGovernance.runtime_installation(database)` now supplies the internal
installation binding through the daemon's existing installation owner. It
validates the canonical receipt against the loaded deployment's payload,
rechecks installation mode/generations and maintenance, requires the actual
daemon runtime root, and binds the actual open `Database` path. A second check
rejects a concurrent governance or deployment-source change; subsequent calls
cannot switch the installation/database association on the same owner.
Resolution is read-only: it does not initialize an epoch, enroll any participant
or change installed authentication. Missing/foreign/stale installation evidence
refuses rather than accepting a client-selected identity or path. Isolated
legacy and namespaced installation controls exercise this helper; they do not
qualify a running installation or native consumer.
Legacy mode also requires the existing entrypoint owner's mutation decision:
a transfer tombstone already present before the loop baseline cannot confer
ownership of the transferred root. An unaffected namespaced root remains usable.

Local storage schema 28 requires the custody tables, nullable associations,
ordinary turn-admission columns and canonical guards. The existing writable
migration owner prepares older databases before publishing that schema version;
the frozen pre-custody-v27 test fixture is independent of current constructors
and Git history. Read-only preparation and existing-only writable adoption
validate version and physical shape without bootstrap, migration or writer
startup. An already-current incomplete custody layout refuses instead of
silently recreating potentially missing closure state. These checks do not
qualify complete custody or retained-target compatibility.

Actual `create_session`, `create_turn` and live-message insertion writes accept an optional retained
producer association. SQLite checks it against the open installation/epoch and
enrolled frontend in the same insertion transaction. The ordered insertion
trigger also refuses unassociated legacy inserts after explicit DB closure.
Closing admission uses the original transition, target assembly and enrolled
predecessor observations, CASes the observed epoch/revision, then advances the
epoch once. Exact closure retries observe the same closed transition; restart
does not reopen it. There is no handback or reopening API in this foundation.

Queued-prompt consumption and turn creation retain their existing atomic
transaction. Refused admission rolls back queue consumption. Native keyed
invocation identity/replay continues to use the existing turn journal.
Association is immutable; associated session/turn/inbox history is protected from
deletion and GC until its actual outcome can be reconciled by a future owner.
Terminal status or a completed turn alone cannot establish native settlement.
Ordinary unassociated writes/deletion remain unchanged while admission is open
or the installation has never been bound.

## Internal inbox handoff owner

Schema 30 adds custody fields to the existing `live_messages` rows. The writable
migration prepares the fields and exact guards before publishing the version.
Read-only preparation, existing-only writable adoption and an already-current
writable open reject missing/changed inbox fields or guards instead of repairing
away possible handoff history. Migrated legacy rows retain null associations;
they do not acquire evidence that an earlier poll returned no body.

`poll_live_message_handoffs` requires the owner's current installation/producer
admission and exact native-session, consumer-incarnation and process-birth
observations. In one short DB transaction it retains a single immutable handoff
on each original message before returning its body. An unassociated/foreign
message or consumer refuses the whole batch. Each row retains its original
enrolled producer independently of the polling frontend, so multiple producers
in the same open epoch do not block each other or rewrite old associations.
Repeated polls observe the original
attempt and outcome; they never renew send permission.

`begin_live_message_handoff` is the DB owner's one-shot final-admission primitive.
It rechecks the open epoch, enrolled frontend and exact live registration, then
commits the original attempt's outstanding state. Alias resolution, another
consumer incarnation and an expired/missing registration cannot supply that
authority. A second call is not admitted, including after restart or a lost
first reply. The interval after this DB commit and any future SDK call remains
outstanding custody, not proof of send or non-delivery.

`record_live_message_handoff` can record the original SDK acceptance, attributed
consumption or unknown outcome after closure or registration cleanup. Acceptance
and consumption retain separate timestamps; neither certifies semantic
completion. An unknown attempt cannot return to an offered state. Handoff
identity/input and recorded timestamps cannot be erased to mint a retry.
Legacy poll/ACK/consumption methods do not send or settle retained handoffs.
Associated messages are excluded from those legacy paths from insertion onward,
not just after a handoff exists. An identical keyed retry must match the retained
producer association, including legacy versus associated admission.
Registration deregistration, takeover and reaping preserve associated inbox
history even when the registration itself goes away.

Qualified control handoff is not implemented. Both message insertion methods and
the canonical SQL guards reject custody-associated `control:` kinds, including
case variants. Legacy control claim refuses an existing unsupported associated
control before expiry or claim mutations; ACK and withdrawal cannot settle it.
Ordinary unassociated control behavior is unchanged.

Custody transactions, including associated inbox insertion, commit with
connection-local `synchronous=FULL` before returning. Both normal and adopted
connections restore their prior setting afterward, including on refusal.
Unrelated event/metadata writes retain their existing `NORMAL` policy. A
one-shot marker that could disappear after SDK acceptance would not fence replay.

These are internal owner methods, **not consumer authentication or an SDK
integration**. A future transport must authenticate/enroll the actual consumer,
negotiate the handoff contract, bind its current native context and call the
final primitive at its actual SDK boundary. It must not cache a true result as
reusable permission, interpret false/timeout as non-delivery, downgrade to legacy
delivery, or rebuild an independent outbox. No public route, ordinary extension
poller, startup or installer currently activates these methods.

## Durable current-consumer enrollment

Schema 31 adds `bridge_inbox_consumers` to the same database. Explicit
`enroll_inbox_consumer` binds an exact local live registration to its current
consumer incarnation, installation and open admission epoch. It does not mint a
credential, bind a client-selected database or retroactively enroll an ordinary
legacy registration. A remote `venue` remains unsupported in this local owner.

Enrollment compare-and-swaps the observed predecessor, retaining both
incarnations. An identical retry after a lost enrollment reply observes the same
current record without changing its revision. A stale predecessor, reused input,
retired incarnation or historical handoff without enrollment refuses rather than
reenrolling it. The extension must mint a new incarnation on extension reload or
SDK/native-session change; its future transport owns observing and reconciling
the current predecessor, not blindly retrying a failed CAS.

Registration deletion, alias rollover and changes to process/native identity,
machine, venue or non-live status retire the old enrollment in that same DB
transaction. Ordinary heartbeats do not enroll or reactivate it. Poll and final
admission require the exact current enrollment as well as fresh registration;
concurrent reload and final admission therefore have one serial order. An
already-committed final attempt stays outstanding after reload, and its original
consumer can still report outcomes. A successor cannot inherit its old handoff.
Unoffered original messages remain intact for the newly enrolled consumer.

Enrollment history is immutable apart from irreversible retirement, survives
registration cleanup and participates in DB revision/inventory observations.
Migration never fabricates enrollment for older registrations or handoffs.
Prepared/current database validation and passive adoption require the exact
table, current-owner index and guards; they never reconstruct missing history.
This remains an internal DB prerequisite: no HTTP registration, ordinary
extension poll, SDK call, installed authentication or running service is changed.

## Inventory is observation, not qualification

DB inventory reads every row's selected identity/status metadata for known
session, turn, creation, acquisition, queue, inbox/control, reservation and
relationship tables in one transaction. It does not include prompt bodies,
truncate terminal rows or create missing lazy tables. A missing table/field is
reported as unknown (`None`), not an empty list; unknown tables and unassociated
history are explicit blockers.

The DB revision covers session/turn/inbox writes, producer and consumer enrollment and the epoch
only. It is not a cross-owner revision for acquisition, actual SDK consumers, native
hosts or arbitrary SQLite writers. An expected-epoch/revision read detects
intervening covered-owner writes but is not a global quiescence certificate.
The report explicitly identifies unmediated cohorts and sets
`restore_qualified` and `acknowledged` to false for this foundation.

`HostIndex.locked_inventory` reads fresh storage under the same native mutex as
ordinary index mutations, without flushing, provisioning an index or replacing
the caller's cached records. Missing index evidence remains incomplete.
Malformed/duplicate/unknown fields, unsafe parents, linked/hard-linked/reparse
files, unsupported record shapes and unstable reads refuse explicitly.
The strict decoder rejects literal nonfinite constants and exponent-overflow
floats at every nesting level, including opaque metadata arrays. Valid finite
values are preserved rather than clamped, dropped or stringified.
`assert_inventory_current` rechecks the observed file identity and private
bytes under that mutex. These are strict storage observations, not monotonic
host admission epochs or verified native host receipts; they always retain
`native_custody_verified=False`.

## Remaining gates

Final acquisition/start/resume/background admission, SDK poll/send/ACK custody,
native-send settlement, host/remote enrollment, cross-owner revision closure,
target-declared state compatibility and continuing installer retention remain
unimplemented beyond the internal inbox primitives above. No existing drain, SDK consumer or installer path is switched to
this foundation. No Dispatch database or adopter effect owner is involved.

The focused tests cover real SQLite serial ordering, queue rollback, original
history, legacy/incomplete inventory and fresh strict host storage. They belong
to the bounded development/integration portfolio; they do not automatically
enter the required unit-only PR packet or prove Windows/native/live operation.
