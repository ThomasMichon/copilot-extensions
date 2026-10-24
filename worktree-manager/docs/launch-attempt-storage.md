# Launch-attempt storage and confirmation

`worktree_manager.launch_attempts.LaunchAttempts` is an internal, local evidence
store. Current source wires Picker confirmation before effects; execution
outcome correlation, inspection commands and machine sweeps remain incomplete.
This does not enable remote prompts or qualify an installed prompt-loss repair.
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
attempt ID never creates a replacement intent; later outcome correlation
remains incomplete.
The attempt ID remains on the Manager decision and `LaunchRequest`; it is not
yet an engine or native-receipt correlation contract.

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

Legacy `SeedHistory` writes still occur, off-thread, after successful journal
persistence and before composer dismissal. This preserves existing history
navigation while migration is pending; it is **not** complete 90-day retention
across all prompt copies. No pending daemon intent is migrated or deleted by
this confirmation gate.

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
maintenance. The store does not schedule expiry or migrate existing
`SeedHistory`; production maintenance, legacy-copy expiry and UI integration
remain required before claiming a delivered retention feature. It never deletes
pending engine/daemon prompt state.

## Reproducible checks

Run the ordinary storage and existing-history units through the repository's
contained test runner. The explicit native lane is:

```text
python tools/run-plugin-tests.py worktree-manager --allow-explicit-tiers --test-file tests/native/test_launch_attempts_native.py
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
