# Launch-attempt storage and confirmation

`worktree_manager.launch_attempts.LaunchAttempts` is an internal, local evidence
store. Current source wires Picker confirmation before effects and Manager-owned
resolution/launcher observations and negotiated remote prompted admission.
Full engine/native outcome correlation, inspection
commands and machine sweeps remain incomplete.
Source-level remote New/cold Resume prompts require the negotiated target-native
contract below; this does not qualify an installed prompt-loss repair.
The implementation and remaining integration are
tracked by the [launch-attempt effort](../../efforts/active/worktree-launch-attempt-journal/README.md).

## Picker confirmation boundary

The production New and cold Resume composers save exact original field text,
options, project and selected venue before dismissing. Ordinary Open/Resume,
Bare Resume, Restore, new-window launches, compact Picker launches, task Open
before embodiment, and provider venue Open use the same confirmation gate.
Confirmed Open/Resume, Bare Resume, new-window and task Open actions that fail
locality resolution retain their selected target/options with unknown locality
and a structured `locality-unresolved` refusal. They neither exit nor start
embodiment or launch. Opening an unavailable Resume prompt editor is not a
confirmation and records nothing. A deferred continuation already carrying an
attempt ID never creates a replacement intent.
The attempt ID remains on the Manager decision and `LaunchRequest`. Remote
prompted New/Resume also pass it through the engine to the selected target's
durable admission. Local and seedless engine/native correlation remain incomplete.

Confirmation immediately shows a saving modal. The existing Picker
`Inbox`/background-worker mechanism performs private storage work off the
Textual message/render thread. The original composer remains mounted beneath
the modal, so neither its text/options nor another confirmation can change
the in-flight snapshot. Allocation, remux preparation, provider handoff and
launch are released only after the save completes and the original mounted
owner is still current. An application exit or removed owner cannot release
that continuation later.

Empty prompts are recorded. Unsupported prompt/option combinations retain
`confirmed` and `refused` observations without dismissing the original composer
or creating a legacy history entry. Cancellation before confirmation records
nothing. Explicit Picker mock mode disables confirmation persistence for both
New and cold Resume; it creates neither journal nor legacy-history entries.
A save failure reports a sanitized error, preserves the draft and
prevents launch; explicitly retrying identical failed input reuses its attempt
ID. A later separate action after a successful confirmation gets a new ID.
The journal preserves raw text independently of existing execution-text
normalization.

`SeedHistory` writes still occur, off-thread, after successful journal
persistence and before composer dismissal. This preserves existing history
navigation with the same 90-day input bound. Neither history nor journal
maintenance migrates or deletes pending daemon intent.

## Manager observations

The same confirmed attempt follows `_resolve_for`, `_run_launch`, Restore,
task embodiment and provider venue handoff. The Manager commits
`resolution-requested` before calling the engine, then `resolved` with only
returned worktree/request/seed identities. This is a returned plan, not
allocation or process-start proof. Engine resolution can mutate before an
error or lost reply; such failures are `unknown`, never evidence that no
worktree was allocated.

Local option/admission refusals are `refused`. Restore and task embodiment
have distinct `*-requested` and `*-returned` observations; they do not
claim that Copilot started. Direct, relocated, remote and AHP launcher calls,
including new-window and provider handoff wrappers, commit `launch-requested`
before invoking the existing owner, then `launcher-returned` with its exit
code if control returns. That code may describe a wrapper or spawned-window
handoff, not Copilot. AHP's requested boundary precedes hosted-session
preparation too, because preparation can create a session and persist its
binding before the final launcher runs. POSIX process replacement and abrupt
process exit may
leave only the requested boundary. Missing completion evidence remains
unknown; the journal never fills it in or replays the action.

An observation failure before the next effect prevents that effect and
reports a sanitized error. After a launcher or embodiment has already
returned, a failed outcome write warns without changing its actual result
into a retry-shaped failure. Exceptions retain a bounded reason code and
propagate through existing error handling, not into journal free text.
These observations do not manufacture native-start or prompt-acceptance receipts.

## Negotiated remote prompted admission

Remote New and stopped Resume composers use the same confirmation gate, without
reading a remote worktree's execution leg from host-local state. The selected
target enforces its own native-provider and cold-start checks. AHP, live-session
prompt injection, bare Resume and unsupported option combinations remain refused.

For a nonempty remote prompt the engine client requires version-2 admission,
the native-seed capability and an exact echo of the saved attempt ID. The target
persists this correlation with its existing request/worktree/seed receipt.
The host records those identities only after verification. The interactive SSH
launch carries the target identities and native fence, not another prompt copy.
An older receiver, missing capability or mismatched receipt has no weaker retry.
The attempt ID does not become an idempotency key: the target's request ID remains
the recovery authority. On an uncertain response, inspect that request on the
target rather than confirming another New or automatically replaying.

This binds remote prompted admission, not every local/seedless engine operation,
Copilot process start or actual prompt handoff. Open behavior and the native
handoff/duplicate-delivery fences are unchanged.

## API and durability

Construct the store with the Manager's existing installation-local private root
(the default) or an explicit root in an isolated test. `record_intent` accepts
the caller's UUID hex identity, action, project, origin surface, locality,
machine/environment or provider/venue identity, selected worktree, exact prompt
and options. The caller allocates the identity before confirmation persistence.
An identical retry is idempotent; changing or expiring its input refuses reuse.

The intent and initial `confirmed` observation commit in one SQLite transaction,
with `synchronous=FULL` and DELETE journaling. An initialized, protected database
is published atomically. Existing unrelated schemas, uninitialized files,
orphaned sidecars, unsafe links, non-regular database files and incompatible journal modes are errors,
not empty history or permission to reset a store. Writer contention uses the
existing private-history convention of a five-second SQLite deadline and
surfaces failure. Callers must preserve drafts and refuse
launch effects until required intent persistence succeeds.

`append` records structured owner/phase observations and a small allowlist of
identity, exit-code and reason-code fields. It refuses free-text diagnostics and
unknown attempts. Owner labels are attribution, not authentication or proof of
acceptance; consumers must validate the actual owner's receipt before appending
such an observation. A plan, zero process exit or missing event never implies a
launched Copilot, consumed prompt or safe replay.
Worktree observation IDs preserve engine-provided Unicode verbatim, including
combining characters. They are bounded opaque identifiers that reject ASCII
whitespace and controls, not filesystem paths or a reason to normalize the engine's identity. Other
observation identity fields retain their existing restricted encoding.

## Private readers and expiry

`entries` returns bounded metadata, with optional project/action/time filters.
`detail` adds bounded structured observations. Neither selects private input.
Metadata includes `is_local` (`true`, `false`, or `null` for unresolved) and
boolean `inputs_available` fields.
Event history is paged with `event_limit` (at most 1000) and `after_sequence`;
`events_has_more` and `next_event_sequence` explicitly identify another page.
Growing accepted history never prevents recovery of unexpired private input.
`detail(..., include_input=True)` explicitly retrieves exact prompt/options
only before the original 90-day confirmation deadline. Corrupt stored metadata,
events or input refuse rather than returning a success-shaped substitute.

Reads open SQLite in read-only mode and verify, rather than repair, private
permissions. Missing storage stays missing. Windows requires the protected
owner/SYSTEM DACL on the directory and main database. SQLite-created sidecars
may safely inherit those same owner/SYSTEM ACEs from the verified private parent
without their own protected-DACL flag. POSIX requires the current owner and
`0700` directory / `0600` file modes. Writers use the existing `private_state`
protection helper; readers never replace a concurrent writer's sidecar ACL.

The SQLite header is inspected before opening a connection, so an unsupported
WAL-mode database is refused without creating WAL/shared-memory bookkeeping.
Schema validation compares every object row, including its type; a trigger
cannot hide behind an index with the same name.

Version 2 preserves the original table/index shape and encodes unknown locality
as `-1`, exposing it only as `null` through the API. Version 1 remains readable
without writes or permission repair. The first writer validates all original
metadata under version 1's stricter boolean-locality rules, then advances the
version in the same transaction as its mutation. A corrupt old `-1` cannot
become a valid unknown merely through migration. Failed migration or mutation
rolls back both together. Older version-1-only software refuses a version 2
store rather than interpreting unknown locality as local.

`expire` physically clears expired input using SQLite secure deletion while
retaining metadata and observations. Reads hide expired input even before
maintenance. Real production Picker use runs both journal and history maintenance
off-thread after first paint, subject to `maintenance.automatic`. Mock and
read-only review never run it. Explicit launch confirmation also maintains both
stores before saving, including in a long-running Picker or with automatic
maintenance disabled. A failure reports only the affected store, preserves the
draft, and prevents that launch; maintenance still attempts the other store.
At Picker entry, the sanitized warning does not suppress independent anchor
repair, housekeeping or heartbeat registration.
Offline or automatic-maintenance-disabled machines physically expire input at
the next permitted maintenance or confirmation opportunity. No scheduler is added.

Recognized unversioned `SeedHistory` databases migrate in place, preserving IDs,
projects, exact unexpired text and original submission timestamps. These remain
prompt-only history, never evidence of an allocation or launch. An indexed expiry
transaction securely deletes expired rows across projects and marks the migration
as awaiting a scrub. A subsequent `VACUUM` removes legacy freelist copies before
the final version is committed; interruption leaves the retryable scrub marker,
not a falsely completed migration. Normal new history uses the same indexed
secure-deletion path. Deduplication and reads never extend an input deadline.
Recognized legacy WAL is checkpointed by SQLite into DELETE mode before migration;
a busy connection produces an explicit failure, not manual sidecar deletion.
Read-only history access refuses WAL until maintenance instead of creating
bookkeeping files. Unexpected WAL sidecars on DELETE-mode stores and orphaned
sidecars with no database, an empty file or another non-WAL header are errors
and remain untouched for diagnosis.
No operation deletes pending engine/daemon prompt state. These are source storage
contracts, not proof of rollout or installed prompt acceptance.

Prompt-history reads validate legacy timestamps before the cutoff query, so
SQLite's type-ordering rules cannot turn malformed text or infinity into an
indefinite deadline. Both composer history loaders carry original expiry
deadlines into navigation caches. Navigation drops expired cached text and edits
without re-reading SQLite or overwriting the current draft; already-expired
entries cannot revive if the clock moves backward. A cached entry's deadline
never restarts when the composer opens.

## Reproducible checks

Run the ordinary storage and existing-history units through the repository's
contained test runner. The explicit native lane is:

```text
python tools/run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests/native/test_launch_attempts_native.py
python tools/run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests/native/test_launch_retention_native.py
```

It exercises abrupt writer exit and an independent reader, interrupted
transactions, actual composer-process exit before save and after commit but
before dismissal, Windows ACL inspection and cold/warm commit measurements.
Its confirmation-handler check imposes slow storage while enforcing a 100ms
handler budget; this is not an end-to-end physical-key latency measurement.
Measurements identify an in-process storage boundary, not a UI gesture or an
installed launch. Storage work can exceed the Picker's acknowledgement budget
under load: confirmation integration must remain responsive while awaiting
durability, without dismissing or allocating early.
The retention native lane leaves a real committed WAL behind after abrupt process
exit, migrates and erases it in a separate process, interrupts immediately before
legacy freelist scrubbing, and proves restart completion without changing retained
timestamps, journal metadata or a synthetic pending seed.
