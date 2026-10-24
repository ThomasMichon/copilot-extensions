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
Ordinary application startup does not call them automatically. The explicit
local negotiation adapter below enrolls only its serving frontend.

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
not just after a handoff exists. Internal explicitly associated insertion
retries must match the retained producer association. The ordinary producer
recovery path below instead returns the original admitted row without acquiring
a replacement frontend association.
Registration deregistration, takeover and reaping preserve associated inbox
history even when the registration itself goes away.

Qualified control and interrupt handoff are not implemented. Retained polling
and final admission refuse interrupt delivery without aborting or resending.
Both message insertion methods and
the canonical SQL guards reject custody-associated `control:` kinds, including
case variants. Legacy control claim refuses an existing unsupported associated
control before expiry or claim mutations; ACK and withdrawal cannot settle it.
Control admission and pending-control enrollment are serialized as described
below; never-enrolled legacy control behavior is unchanged.

Custody transactions, including associated inbox insertion, commit with
connection-local `synchronous=FULL` before returning. Both normal and adopted
connections restore their prior setting afterward, including on refusal.
Unrelated event/metadata writes retain their existing `NORMAL` policy. A
one-shot marker that could disappear after SDK acceptance would not fence replay.

These internal owner methods are **not consumer authentication or an SDK
integration**. The local transport below composes them with the established
access boundary. The ordinary extension poller and installer do not activate
them. A future SDK integration must call the final primitive at its actual
effect boundary, never cache a true result as reusable permission, interpret
false/timeout as non-delivery, downgrade to legacy delivery or rebuild an
independent outbox.

## Durable current-consumer enrollment

Schema 31 adds `bridge_inbox_consumers` to the same database. Explicit
`enroll_inbox_consumer` binds an exact local live registration to its current
consumer incarnation, installation and open admission epoch. It does not mint a
credential, bind a client-selected database or retroactively enroll an ordinary
legacy registration. A remote `venue` remains unsupported in this local owner.

Schema 32 adds the nonpartial consumer-session history index and partial
retained-message session index used by ordinary admission.
Current-consumer selection uses the existing unique current index; a separate
indexed history-existence check preserves retired-target refusal without loading
all incarnations. The retained-row fallback uses its partial index rather than
scanning a never-enrolled target's old unassociated messages. The normal writable
migration preserves every enrollment, message and handoff. Passive adoption
requires the prepared schema and exact indexes; neither read-only open nor
adoption creates them.

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
Unoffered original messages remain intact for a newly enrolled consumer of the
same native session. A native-session rename does not move associated messages
or handoffs to its successor; only unassociated legacy messages follow aliases.
The original retained target, input and producer association therefore survive
alias cleanup, and an original-URL keyed retry still recovers that same row
after deregistration or purge without granting a new send opportunity.
While aliases still exist, retained admission, keyed recovery, receipt reads
and pending-input inspection also use the original native target. An original
URL cannot downgrade a retired enrollment to the successor's legacy queue,
and the successor cannot recover the predecessor's retained operation key.
Unassociated legacy rows continue to follow their routing aliases.
Pending-input inspection first narrows indexed session candidates, so an empty
inbox does not scan unrelated retained history to apply custody-aware matching.
Readiness-bound startup, standalone readiness and mode-control admission retain
that original target too: an alias cannot insert an unsupported control into a
successor's legacy queue or reach its readiness waiter.

Enrollment history is immutable apart from irreversible retirement, survives
registration cleanup and participates in DB revision/inventory observations.
Migration never fabricates enrollment for older registrations or handoffs.
Prepared/current database validation and passive adoption require the exact
table, current-owner index and guards; they never reconstruct missing history.
Ordinary HTTP registration and extension polling remain separate from explicit
retained enrollment. No SDK call or installed authentication is changed.

## Explicit local transport adapter

HTTP generation 48 introduces the `retained-inbox-v1` request contract under
`/api/v1/live-sessions/{session_id}/retained-inbox`. It is a source-level
adapter, not full runtime restoration or actual SDK qualification.
Consumers must require `RETAINED_INBOX_PROTOCOL_VERSION` (48) before using it;
an older or unversioned receiver is unsupported, not a legacy-delivery fallback.
Every request passes the ordinary shared-token and request-custody middleware.
This first adapter supports only the admitted loopback endpoint with a local
peer and no forwarded/proxy headers. Public binds, nonlocal peers and proxy
ingress refuse before enrollment or inbox exposure. Loopback is an additional
scope check, not a replacement for user access controls. Container proxy
qualification and other endpoint transports remain open.

| POST suffix | Existing owner operation |
|-------------|--------------------------|
| `/negotiate` | Resolve the daemon's actual installation, explicitly initialize its open DB epoch if absent, enroll only its actual frontend process, and observe the current registered consumer predecessor |
| `/enroll` | Compare-and-swap that observed predecessor through `enroll_inbox_consumer`; exact retries retain their original predecessor |
| `/poll` | Commit original row-bound handoffs before returning associated message bodies |
| `/begin` | Recheck installation, current frontend/consumer and open epoch; return one invocation-only admission result |
| `/outcome` | Record the exact original handoff's SDK acceptance, attributed consumption or unknown outcome, without acquiring another send opportunity |

`LiveInboxRuntime` is constructed without storage mutation. Only explicit
negotiation initializes/enrolls the frontend; ordinary startup, registration and
heartbeats do not. Frontend observations use this serving process's real PID,
native birth and fresh incarnation. Subsequent operations recheck installation,
process and the same open `Database` owner. A request echoes the server-derived
installation/epoch only as a wrong-target/replay fence; it cannot select a
database, installation root or producer.

The consumer supplies its exact SDK-session/process/incarnation observations
and the supported contract. Registration must already be fresh, local and exact,
never an alias. A reloaded extension must mint a new incarnation and explicitly
negotiate its predecessor. A lost enrollment reply is retried with the original
request, not with a newly observed predecessor used to overwrite another owner.
Unknown contracts, extra owner-selection fields and coerced identity values
refuse rather than silently selecting a legacy path.

Polling and final admission require a serving frontend. Original outcome
reporting remains available through the admitted endpoint after closure,
draining, registration cleanup or consumer retirement; it neither needs current
enrollment nor reinitializes it. Original message, handoff and consumer identities
must match. Lost final-admission replies remain outstanding and cannot be
converted to a second true result by repeating `/begin`.

The actual SDK consumer is not switched to this adapter yet.
Unassociated legacy inbox rows remain unqualified and
refuse a retained batch without partial handoff commits. No SDK event,
consumption evidence, automatic replay, proxy implementation or native behavior
is inferred from these ASGI/installation/real-SQLite controls.

## Ordinary producer admission

HTTP generation 49 advertises `RETAINED_INBOX_PRODUCER_PROTOCOL_VERSION` for the
ordinary admission/recovery guarantee below. A producer relying on it must
require that floor through the daemon's advertised health version. The
retained-inbox transport floor of 48 alone is insufficient: its original
receiver leaves ordinary message ingress unassociated. Unversioned and older
receivers cannot qualify this guarantee from a successful ordinary response.
The supported minimum and every earlier capability floor remain unchanged.

`POST /api/v1/live-sessions/{session_id}/messages` now selects retained custody
when the durable target enrollment requires it. The existing atomic freshness
owner selects the current consumer and original frontend association in the
same SQLite write transaction as insertion. This requires an original nonblank
operation key and currently serving admitted local frontend; it does not
initialize an epoch or enroll anyone. Missing, retired, foreign or stale
observations refuse before admission, never fall back to a legacy row.
A never-enrolled target remains on the ordinary legacy path, independently of
another session's enrollment.

An identical keyed retry observes its original message, payload and producer
association before considering a fresh admission. It can therefore recover a
lost admission reply after frontend replacement, closure or registration
cleanup. Changed input refuses; neither recovery nor the returned admitted
receipt grants an SDK invocation or implies acceptance or consumption.
The original row survives a real producer-process exit before its reply.

Retained startup, interrupt and control effects remain unsupported. The
existing freshness insertion statement refuses their unqualified admission
to an enrolled target, including readiness controls before a startup prompt.
Enrollment refuses pending legacy controls, so control insertion and enrollment
have one serial order; it never retires or silently settles the control.
Direct internal owner methods and other producer cohorts are not thereby
qualified. Full SDK wiring, outcome/consumption recovery and native/platform
evidence remain open.

## Original handoff recovery

HTTP generation 50 advertises `RETAINED_INBOX_RECOVERY_PROTOCOL_VERSION`.
Consumers requiring recovery or bounded polling must check that floor; 48
qualifies the transport and 49 qualifies ordinary producer association, neither
qualifies these additions. Retained delivery remains explicitly opted in;
ordinary registration and legacy extension delivery defaults are unchanged.

`GET /api/v1/live-sessions/{session_id}/retained-inbox/recovery` reads pending
original handoff metadata, oldest first, with `limit` (1–100, default 50) and
exclusive `after` message-ID pagination. `next_after` names the next page's
exclusive boundary, or is null when that observation has no further page.
Start a later reconciliation pass at zero: consumption may change between
pages, and an empty page is not complete runtime custody or quiescence.
`GET .../recovery/{message_id}` also retains a consumed handoff's receipt, so
a lost outcome reply does not require reenrollment or a new send.

Both reads use the existing receipt owner and original SQLite rows, exact
native session IDs rather than aliases, and the same admitted local/authenticated
endpoint boundary. They expose the original handoff ID, consumer observations,
stage and timestamps, never the message body, producer admission or final-send
permission. Reads remain available from an already prepared passive database
after closure, consumer retirement, registration cleanup or frontend draining.
They perform no initialization, enrollment, repair, reopening or cursor movement.
Malformed original associations refuse rather than being skipped or repaired.
The legacy message receipt keeps its unchanged response shape.

The existing `/poll` accepts an optional strict `limit` of 1–100. A consumer can
request one message at a time, retaining only that original handoff before a
possible crash. Omitting the field preserves the earlier batch behavior.
Repeated polls do not mint new attempts or recover a lost true `/begin` response.
An old consumer's unresolved handoff remains its original observation, not a
send opportunity for a newly enrolled successor.

Schema 33 adds a partial pending-handoff index through normal writable
preparation. The read projection selects no bodies and avoids scanning consumed
history. Migration preserves original rows; passive adoption refuses an older
schema and damaged current indexes without DDL or repair. These source controls
are prerequisites for the still-open actual SDK consumer integration.

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
unimplemented beyond the inbox owners and explicit local adapter above.
No existing drain, SDK consumer or installer path is switched to this foundation.
Only the bounded ordinary inbox-producer route above selects enrolled custody.
No Dispatch database or adopter effect owner is involved.

The focused tests cover real SQLite serial ordering, queue rollback, original
history, legacy/incomplete inventory and fresh strict host storage. They belong
to the bounded development/integration portfolio; they do not automatically
enter the required unit-only PR packet or prove Windows/native/live operation.
