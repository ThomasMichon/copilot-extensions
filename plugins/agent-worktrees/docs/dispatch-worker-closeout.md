# Own-worker Dispatch closeout provider

The executing Worktrees runtime exposes an additive provider at
`python -m agent_worktrees.dispatch_worker_closeout`. Here `python` means
the interpreter of the **selected Worktrees runtime**, not an ambient
interpreter or a newer payload's source tree. The canonical `finalize` CLI
also delegates to this same provider using `--dispatch-task-id`,
`--dispatch-reservation-key` and `--dispatch-native-id`; it retains JSON output
and refuses ordinary worktree selectors, config overrides, dry-run, abandon,
handoff, open-PR override or landing-explanation options in this mode.
An explicit global project override is refused before it can change cwd.
The caller's actual cwd alone selects the existing managed project.

`--describe-contract` performs no finalization and returns
`agent-worktrees.dispatch-worker-closeout`, version `1`, with operation
`finalize-own`, native proof `inuse-hold-v1` and qualified platforms
`linux`/`wsl`. The descriptor explicitly excludes paired closeout, task release
and process retirement. Linux containers require the native source owner's
[namespace-scoped execution epoch](engine-picker-contract.md#original-native-session-observations)
and the same actual open native hold; a container marker alone is not proof.
Unsupported platforms, inaccessible native namespaces, missing canonical
native registration and older runtimes have no equivalent legacy fallback.
Consumers must establish support from the exact executing runtime before
requesting closeout; a package version or a payload declaration is insufficient.

The effectful form requires `--task-id`, `--reservation-key` and `--native-id`.
It derives the worktree exclusively from the caller's actual current directory.
The module entrypoint resolves and binds that directory's existing project
before configuration loading, restoring any prior in-process project afterward.
Discovery does not resolve or bind a project.
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

## Read-only owner observation

The selected Worktrees runtime also supports
`record --describe-closeout-contract`, returning the same contract identity
and version with operation `observe-finalized`. This additive read surface
does not imply that an older runtime's ordinary `record` response contains
closeout authority. Discovery never inspects or finalizes a checkout.
The descriptor additionally advertises `own_finalization_cli_versions: [1]`
for the canonical `finalize` form. Older observation-only descriptors remain
valid for observation but cannot qualify the new effectful syntax.

Observation uses the explicit project and exact `--worktree-id` together with
`--closeout-task`, `--closeout-reservation` and `--closeout-native`. Partial or
mixed discovery/observation requests refuse. The record owner rechecks its
local execution authority under its existing record lock, actual managed
checkout, immutable allocation, native head/source, outbound custody and
foreign-session preservation gates. It then verifies the actual clean
checkout against the existing finalized snapshot. The observer need not be
the worker process and never invokes the finalizer. Missing, dirty, changed,
protected or uninspectable state is not a successful observation.
Snapshot creation and comparison isolate Git from inherited repository,
worktree, index and configuration selectors; the explicitly named checkout,
not an alternate clean repository from the caller's environment, is inspected.

The reply identifies the project/path, worktree creation nonce, complete
Dispatch allocation, native head revision and observed finalized checkout.
It remains a point-in-time read, not a checkout lock held across a remote
queue transaction, task-release operation or proof that the runner exited.
Task release belongs to the coordinator's separately fenced transaction;
physical retirement remains with its existing owner.

## Internal nonfinalizing readiness

`dispatch_worker_readiness.observe_ready` is a source-only expansion for an
active allocator-created Linux/WSL workspace. The selected runtime exposes it
through the separate readiness contract below; neither existing version-1
closeout descriptor changes meaning. Ordinary Dispatch submission does not
consume it yet. It never calls finalization, saves a record, settles a claim,
changes task state or retires a process.

The observer requires the original creation nonce, native head revision and
exact retained task callback reference. It checks the actual managed checkout
and reuses own-worker allocation, native-source and preservation admission.
Only one actual stored, canonical, bound version-2 task relationship can be
excepted: its task, original queue, audited assignment, request, creator and
worker must agree with the protected reference. Normal finalization still
blocks this same claim. Other unsettled claims, unknown metadata and offered
handoffs remain blocking; even PR claims need settlement by their normal owner.

Every tracked PR is freshly read through the configured authority, including
locally cached merges. An open, unmerged, incomplete or unobservable PR blocks.
Fresh merged heads feed the existing per-branch committed-content boundary
check without modifying persisted PR metadata. Tracked branch revisions are
fully qualified so a same-named tag cannot mask unpublished work. Only locally
resolvable published bases enter the proof; none available is a blocker.
With no tracked PR, the actual
head and tracked worktree branch must be ancestors of the configured published
base. This conservative ancestry proof does not accept an untracked squash or
patch-equivalent publication. No fetch, probe commit, source repair or automatic
publication is performed. Inherited Git repository/index/configuration selectors
refuse before the existing merge-boundary probes; observation does not alter
process-global environment.

The observer captures custody and checkout under the existing record lock,
releases it for provider/content observation, then reacquires it to recheck
current configuration, custody, clean checkout and local branch/remote-ref
identity before returning. Network/provider I/O never holds that lock.
`own_caller=True` additionally requires the actual
invoking native process and cwd both before and after the content observation.
Independent observation does not require the observer to impersonate that
process. Neither form proves runner exit or freezes arbitrary out-of-band
filesystem writes. Queue advancement must separately bind a new observation
to its retained preparation outside the SQLite write transaction, then recheck
task admission and ownership inside that transaction.

Focused source controls are in `tests/test_dispatch_worker_readiness.py`.
Negotiated caller support, manual or absent-worktree applicability,
Windows/native qualification and rejected-worker recovery remain pending.
Worker decline uses `observe-ready` before its requeue, then finalizes only
after Dispatch settles the claim (see
[retained task-claim ordering](../../agent-dispatch/docs/pool-worker-decline.md#retained-task-claim-ordering)). The ordinary assignment writer remains disabled.

### Selected-runtime readiness transport

`record --describe-readiness-contract` returns
`agent-worktrees.dispatch-worker-readiness`, version `1`, with operations
`observe-ready` and `check-own-ready`, native proof `inuse-hold-v1`, platforms
`linux`/`wsl`, and explicit false paired-readiness, finalization, task-release
and process-retirement facts. Discovery does not inspect a workspace.
Use the **selected Worktrees runtime's** canonical command, with the explicit
project; a newer payload's package version or descriptor is not support.

Both operations require exact `--worktree-id` and all of:
`--readiness-operation`, `--readiness-task`, `--readiness-reservation`,
`--readiness-native`, `--readiness-creation-nonce`,
`--readiness-head-revision` (an integer) and `--readiness-task-claim-ref`.
`observe-ready` is an independent owner observation; `check-own-ready` adds
the invoking native-process/cwd check. The latter is not a worker-supplied
receipt that the coordinator may trust in place of its own observation.
Partial requests, discovery combined with request fields, initialization-directory
selection, mixed finalized-closeout requests and unknown syntax refuse.

Success exits zero with JSON `ok: true`, the readiness contract/version and
selected operation, plus the observer's actual allocation, workspace/head,
retained claim and current checkout/landing evidence. `ready: true` remains
distinct from `finalized: false`; the claim and checkout remain retained.
Refusal exits nonzero with `ok: false` and an actionable `error`, not a
success-shaped legacy observation.

This is provider-first expansion, not ordinary caller activation. Existing
record/initialization and finalized-closeout forms retain their meanings.
Consumers must negotiate this separate contract before emitting readiness
syntax. Older or rolled-back providers lack that declaration; their finalized
observation/finalizer is **not** an equivalent fallback. Preserve responsibility
and surface unsupported readiness before queue effects. Caller migration,
compatibility selection and guarded queue advancement remain separate gates;
no old form is retired by this expansion.

## Remaining integration and validation

This provider expansion does not apply exclusions, requeue a task or retire a
native runner. The ordinary Dispatch CLI/prompts and existing supervisor now
consume the providers conditionally; a caller's copied JSON is not authoritative
Dispatch release evidence. The
[local coordinator boundary](../../agent-dispatch/docs/pool-worker-decline.md#coordinator-owned-local-closeout)
observes this selected runtime before atomic routing/requeue, using
nonfinalizing readiness when the workspace retains its task claim; remote-host and
installed/native worker qualification remain separate work under the
[assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md).

Focused controls live in `tests/test_dispatch_worker_closeout.py` and
`tests/test_dispatch_worker_closeout_integration.py`. The latter traverses the
real finalizer and isolated Git fixtures, including source/PR/outbound refusals
and own-worker checks at freeze, cleanup and commit. Native-process
corroboration is substituted at its boundary there; these tests are not
installed/native worker qualification. Both remain in the periodic isolated
plugin lane, outside required promotion-unit CI.
