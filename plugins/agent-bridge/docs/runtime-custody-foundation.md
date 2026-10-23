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

Local storage schema 28 requires the custody tables, nullable associations,
ordinary turn-admission columns and canonical guards. The existing writable
migration owner prepares older databases before publishing that schema version;
the frozen pre-custody-v27 test fixture is independent of current constructors
and Git history. Read-only preparation and existing-only writable adoption
validate version and physical shape without bootstrap, migration or writer
startup. An already-current incomplete custody layout refuses instead of
silently recreating potentially missing closure state. These checks do not
qualify complete custody or retained-target compatibility.

Actual `create_session` and `create_turn` writes accept an optional retained
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
Association is immutable; associated session/turn history is protected from
deletion and GC until its actual outcome can be reconciled by a future owner.
Terminal status or a completed turn alone cannot establish native settlement.
Ordinary unassociated writes/deletion remain unchanged while admission is open
or the installation has never been bound.

## Inventory is observation, not qualification

DB inventory reads every row's selected identity/status metadata for known
session, turn, creation, acquisition, queue, inbox/control, reservation and
relationship tables in one transaction. It does not include prompt bodies,
truncate terminal rows or create missing lazy tables. A missing table/field is
reported as unknown (`None`), not an empty list; unknown tables and unassociated
history are explicit blockers.

The DB revision covers session/turn writes, producer enrollment and the epoch
only. It is not a cross-owner revision for acquisition, SDK consumers, native
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
unimplemented. No existing drain, SDK consumer or installer path is switched to
this foundation. No Dispatch database or adopter effect owner is involved.

The focused tests cover real SQLite serial ordering, queue rollback, original
history, legacy/incomplete inventory and fresh strict host storage. They belong
to the bounded development/integration portfolio; they do not automatically
enter the required unit-only PR packet or prove Windows/native/live operation.
