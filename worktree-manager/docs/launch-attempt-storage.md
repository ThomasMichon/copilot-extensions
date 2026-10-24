# Launch-attempt storage foundation

`worktree_manager.launch_attempts.LaunchAttempts` is an internal, local evidence
store. It is not yet wired to Picker confirmation, launch execution, inspection
commands or a machine sweep. Installing this foundation does not fix prompt
loss or enable remote prompts. The implementation and remaining integration are
tracked by the [launch-attempt effort](../../efforts/active/worktree-launch-attempt-journal/README.md).

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
Metadata includes boolean `is_local` and `inputs_available` fields.
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

`expire` physically clears expired input using SQLite secure deletion while
retaining metadata and observations. Reads hide expired input even before
maintenance. The foundation does not schedule expiry or migrate existing
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
transactions, actual Windows ACL inspection and cold/warm commit measurements.
Measurements identify an in-process storage boundary, not a UI gesture or an
installed launch. Storage work can exceed the Picker's acknowledgement budget
under load: confirmation integration must remain responsive while awaiting
durability, without dismissing or allocating early.
