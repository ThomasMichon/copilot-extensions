# Own-worker Dispatch closeout provider

The executing Worktrees runtime exposes an additive provider at
`python -m agent_worktrees.dispatch_worker_closeout`. Here `python` means
the interpreter of the **selected Worktrees runtime**, not an ambient
interpreter or a newer payload's source tree. No ordinary Dispatch caller
selects this provider yet.

`--describe-contract` performs no finalization and returns
`agent-worktrees.dispatch-worker-closeout`, version `1`, with operation
`finalize-own`, native proof `inuse-hold-v1` and qualified platforms
`linux`/`wsl`. The descriptor explicitly excludes paired closeout, task release
and process retirement. Unsupported platforms, containers, missing canonical
native registration and older runtimes have no equivalent legacy fallback.
Consumers must establish support from the exact executing runtime before
requesting closeout; a package version or a payload declaration is insufficient.

The effectful form requires `--task-id`, `--reservation-key` and `--native-id`.
It derives the worktree exclusively from the caller's actual current directory.
The existing project metadata reader and execution-space mutation authority
must agree on that managed checkout. Its immutable `dispatch_attempt` must
match the task/reservation and execution machine. The current native UUID must
be the canonical active head with a unique active source: an actual open native
hold in the invoking process's captured ancestry, not an environment label or
a stale PID-shaped file. Rechecks retain the original creation nonce, complete
allocation, native process source and head revision.

Pairing, pending handoff, an open itemized or legacy follow-up, an active effort,
active controllers, unknown backend metadata, another active recorded/live session, and unsettled
outbound responsibility block this narrow provider. Only the worker's own
session claim is exempt from that additional outbound check. PR claims are
deferred to normal authoritative reconciliation before the unchanged
obligation/freeze gates; an open or unobservable PR claim still blocks.
Its allocator's
inbound ownership does not deadlock closeout. This is not a general permission
to finalize a paired, shared or remotely controlled workspace.

The provider calls the normal finalizer with additional admission checks at
entry, before the finalizing freeze, under cleanup locks and before the final
status write. PR reconciliation also checks fresh own-worker admission.
Canonical identity is already required, so this path does not opportunistically
seal a different session's identity or settle the caller's session claim before
successful closeout. Normal source, dirty-content, PR, producer, claim-handoff,
execution-space and runtime-resource gates remain in force. No abandon,
open-PR override or direct finalized-status write is used.

Foreign native holds are inspected independently of asserted lifecycle state:
a concluded or handed-off session can still have a real process holding its
file. Only the exact invoking process source is exempt, not its entire UUID:
an earlier activation can still hold another native file under the same UUID
after a resume infers its end. Inspection targets only this record's indexed
sessions, with limits of 256 sessions, 4,096 directory entries and 64 native holds per check. Held,
unreadable, malformed or over-budget observations refuse; a stale unheld file
does not manufacture a live process. The positive ownership API remains
boolean-compatible, while this preservation check retains an explicit unknown
result instead of treating failed corroboration as absence.
Disappearance during a matching descriptor's fresh recheck remains unknown,
not proof that no other descriptor holds the file.

Successful finalization retains the checkout and live native session. Before
returning JSON success, the provider rechecks current ownership and the actual
clean checkout against its finalization snapshot. The reply identifies the
task/reservation, native session, worktree incarnation and head revision.
It explicitly reports `task_release: false` and `process_retirement: false`.
Failure is a nonzero exit with structured JSON and diagnostic stderr; an
unconfirmed outcome retains responsibility, not permission to replay.

## Remaining integration and validation

This provider expansion does not apply exclusions, requeue a task, retire a
native runner, supply a coordinator-authorized closeout receipt or change
ordinary worker CLI/prompts. A caller's copied JSON is not authoritative
Dispatch release evidence. Coordinator-owned closeout observation and atomic
routing/requeue remain separate work under the
[assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md).
The [Dispatch decline provider](../../agent-dispatch/docs/pool-worker-decline.md)
continues retaining the assignment and reservation until that integration lands.

Focused controls live in `tests/test_dispatch_worker_closeout.py` and
`tests/test_dispatch_worker_closeout_integration.py`. The latter traverses the
real finalizer and isolated Git fixtures, including source/PR/outbound refusals
and own-worker checks at freeze, cleanup and commit. Native-process
corroboration is substituted at its boundary there; these tests are not
installed/native worker qualification. Both remain in the periodic isolated
plugin lane, outside required promotion-unit CI.
