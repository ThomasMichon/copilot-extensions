# Conditional replacement of a stopped native head

`agent_worktrees.conditional_head_replacement` provides a local typed owner API,
`CONTRACT_VERSION = 1`. It promotes an **already registered** new conversation
when a caller has explicit authorization to replace a stopped predecessor.
Registration alone still never displaces an occupied head.
Normal registration can already reclaim a **provably dead** head. Callers must
observe whether it did so before requesting explicit promotion; this command
does not change registration's existing behavior or apply a second transition.

Call `replace_dead_head(HeadReplacement(...))` with:

| Field | Required evidence |
|---|---|
| `worktree_id`, `worktree_path` | Exact tracked identity and absolute checkout root, not a prefix or child directory |
| `creation_nonce` | Existing record's creation nonce; absent legacy nonces are refused, never minted here |
| `expected_head`, `expected_head_revision` | Exact native UUID and positive revision from the existing head-transition ledger |
| `expected_lifecycle_revision` | Current owner lifecycle revision, observed after registering the replacement |
| `replacement_session` | Different, canonical native UUID already registered on that record, never previously head |
| `replacement_source` | Exact source identity of its current, unambiguous, open native activation |
| `native_source` | Existing native lifecycle source witness, checked against the current execution epoch and the process's actual open native session hold |

`record --worktree-id <exact-id> --json` includes the persisted
`head_revision` and `lifecycle_revision` in its `worktree` object. These
additive fields are `null` when absent from a legacy record; the read does not
mint or repair them. Present malformed counters fail the exact record read.
They are observations, not replacement permission or proof of a consistent
history. The replacement transaction still checks the complete history and
current revisions under its locks. A caller must refuse missing revision
support from an older runtime rather than infer counters from other fields.

The native owner's recorded workspace must also match the checkout. Caller
strings, a registration entry, a process ID, or a stale lock pathname alone do
not prove ownership. Linux/WSL uses the open file's device/inode and process
birth. Windows asks Restart Manager which process has that exact file open,
matches both process ID and creation time, then rechecks file identity and
process birth. It only queries resources and closes its temporary observation
session; it never asks Restart Manager to stop or restart an application.
An unavailable query or changing/over-budget owner list refuses.

Replacement checks read the native workspace before sending the first prompt.
At that point Copilot has written `workspace.yaml`, but may not have created
conversation history yet. Only these independently corroborated candidate
checks include that unstarted session. Ordinary session reads and listings
still exclude directories without conversation data.

Windows original-hook capture reads process birth and parent from the kernel.
A parent created after its child is a reused ID and refuses. If a protected
service ancestor cannot be read, only the already-verified prefix is retained;
that prefix still needs the exact open-hold owner to qualify. Machine identity
and the kernel boot identifier separate native execution epochs. The standalone
hook copy uses the same source file, without importing the installed runtime.
Containers, inaccessible ownership evidence and other unsupported environments
remain unqualified. Other facilities' narrower platform gates, including
restart recovery and engine launch-observation receipts, remain unchanged.

## Atomicity and refusal

The operation resolves receiving-side registration authority, then acquires
`FinalizeLock`, the worktree lifecycle guard, launch-admission guard, and the
required cross-process record lock, in that order. Inside those locks it reloads
the record, checks authority again, and validates every condition before writing.
The normal append-only head-transition helper allocates the next lifecycle
revision. The normal record writer persists one transition with reason
`authorized-dead-head-replacement`, including its usual head cache and session
relation revisions.

It refuses a stale head/lifecycle revision or creation nonce, wrong checkout, pending
handoff/delivery, paused or non-active/finalizing/terminal/reaped worktree, external execution
binding, opaque or inconsistent history, exhausted history capacity, live mux,
unknown mux observation, live/unknown predecessor or another live/unknown registered holder. Missing or
ambiguous replacement activation and native proof also refuse. History-capacity
refusal prevents this operation from triggering ordinary ledger pruning.

`HeadReplacementResult` returns `previous_head`, `head_session`, and
`head_revision` only after the save succeeds. Refusals raise
`SessionLifecycleError` (a `ValueError`); lock, authority, I/O and persistence
errors propagate without a fallback. Failed checks leave the record unchanged.
The standard locks may create their usual lock files; those are not session
history or authorization. Replaying a successful request refuses. After an
uncertain persistence outcome, read the owner's head ledger instead of retrying
with a new expected revision.

Old session entries, activations, transcript files and claims remain intact.
The predecessor is neither concluded nor marked handed-off. This is a head
change only; it does not transfer responsibility, authorize task delivery, move
claims, start/retire native conversations or services, or issue proof that no
process was launched.
The existing read-only mux probe executes a bounded, headless `list-sessions`
query; no native conversation or service is started.

## Integration and compatibility

The explicit command `replace-dead-head capabilities` returns
`{"version":1,"contract":"agent-worktrees/conditional-head-replacement","versions":[1],"candidate_observation":1,"preflight":1}`
from the runtime executing the command. Discovery does not read a worktree,
register a session, or promote a head. An older runtime rejecting this command
means replacement support is unavailable; ordinary binding remains available.

`replace-dead-head check --stdin` reads the exact target before a provider starts
a candidate. Its JSON uses the same contract and version, and only the first
six fields in the table: workspace identity, creation nonce, predecessor and
both revisions. It checks the existing history and target under the record
lock and requires every registered native holder and the mux to be stopped.
Success returns `eligible: true` and the exact `target`. This is a current
observation, not a reservation or permission; providers must recheck immediately
before launch, and conditional promotion independently rechecks after launch.
It never registers, starts, promotes or concludes a conversation.

`replace-dead-head observe --worktree-id <id> --session-id <native-uuid> --pid <pid>`
is a read-only way to obtain the candidate fields for the request below. The
process ID is only a lookup hint. The owner reads that process's current kernel
birth and execution epoch, verifies its open native session hold, and requires
the result to reproduce the source identity recorded by the original session
hook. It also checks the native workspace and rereads the registration to reject
a concurrent change. A reused process ID, changed boot, missing hold or ambiguous
activation refuses; the observer never captures its own ancestry. The returned
witness contains only the corroborated original process, not an inferred parent
chain. This does not promote the candidate or authorize its promotion.
The candidate object also includes the last two `head_transitions`, for
checking an uncertain promotion outcome against the exact predecessor revision
and transition reason. Normal registration records `reclaim`; this explicit
command records `authorized-dead-head-replacement`. These are read-only evidence;
omit this field when constructing the strict `apply` request.

`replace-dead-head apply --permit --stdin` accepts one JSON object containing
`contract`, integer `version: 1`, and exactly the fields in the table above.
Unknown, missing, duplicate or mistyped fields and inputs over 65,536 characters
are refused. No fields are inferred from the caller's environment or current
directory. `--permit` records the caller's explicit intent; providers still
must verify their own control authorization before invoking the command.
The native witness is evidence corroborated by the owner, not an authorization
token. The command never captures a substitute witness from its own process.

Success exits zero and returns `contract`, `version`, `replaced: true`, and
the three result fields described above. Missing permission and invalid CLI
syntax exit 2. Invalid JSON, unsupported contracts, owner refusals and
unconfirmed persistence exit 3 with an `error` object. A failure is never
permission to fall back to bind or retry with a newly observed revision.
After an uncertain write, inspect the head ledger as described above.
No older contract is removed; version 1 is the only supported request version.

No tracking-write wire verb is added. Ordinary `bind-session`, registration
and sessionStart keep their head-selection rules; Windows can now
qualify the original native activation source where it was previously unknown.
The local typed API is deliberately separate from registration: the provider
must establish the new native session and register it as non-head first, then
submit its exact owner-observed identity. Failure leaves that registration
intact and does not silently promote or remove it.

[Dispatch's explicit replacement flow](../../agent-dispatch/docs/session-acquisition.md#authorized-replacement-in-the-same-workspace)
uses this owner through [Bridge protocol 52](../../agent-bridge/docs/session-acquisition.md#authorized-native-replacement).
Dispatch grants the task-scoped permission; Bridge starts and checks the native
conversation before asking this owner to promote it. Both must discover support
from the selected executing Worktrees runtime and refuse missing support.
Neither may substitute plain bind on an older runtime. Ordinary recovery and
registration do not acquire this exception. Upgrading this local primitive
alone does not activate the separately deployed callers.

## Focused validation

Run the new owner tests through the repository's contained runner:

```text
python tools/run-plugin-tests.py agent-worktrees --allow-explicit-tiers --test-file tests/test_conditional_head_replacement.py
```

They cover unchanged-state refusals, normal non-head registration, retained
history/claims, native source and workspace validation, replay, contention, and
head changes by a separate process between discovery and record-lock acquisition.
Native hold-probe implementations have their existing focused source/liveness
tests; the new suite does not launch Copilot, mux or any harness service.
`tests/native/test_windows_session_hold_native.py` additionally checks actual
Windows open/closed/unrelated files, process birth, and standalone hook capture
through a bounded child interpreter. It uses disposable session files and does
not register a live conversation or alter installed state.
