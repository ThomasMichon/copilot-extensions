# Pool worker decline and local closeout observation

The explicit provider can retain a current worker's decline **before** its own
workspace closeout. This is not legacy `yield`, successful finalization,
requeue permission or native retirement. The
[claim/start contract](pool-claim-start.md) advertises
`worker_decline_preparation_versions: [1]` separately from
`worker_decline_local_closeout_versions: [1]` and
`worker_decline_closeout_complete: false`. Local observation is an additive
provider boundary, not complete remote-host or installed/native qualification.

## Ordinary worker and existing supervisor

Fresh ordinary pool workers receive a workspace allocated through the
Dispatch-aware Worktrees owner, with immutable attempt attribution and
coordinator `created` ownership. Bridge embodies that exact workspace rather
than allocating an unattributed standalone one. Historical standalone
allocations without those facts remain blocked; they are not relabeled or
retroactively adopted by a decline request.

The ordinary `decline` command requires `--pool-generation`,
`--owner-session-id`, a meaningful `--note`, and either repeatable exact
`--exclude` scopes or `--exclude-self worktree|machine`. It resolves its own
machine/worktree and requires the actual current native session environment.
Host comparison uses existing normalization; machine self-exclusions retain
the exact pool-assignment machine from task detail, not the caller's spelling.
It reads the original coordinator's current task/reservation, negotiates
preparation and records the decision before attempting its own closeout.
Task-only credentials remain outside this pre-acceptance operation.

The command selects the existing Worktrees launcher without rewriting a Python
module name or assuming a launcher shape. Its read-only `record` descriptor
must advertise `own_finalization_cli_versions: [1]` before the command emits
the canonical own-worker `finalize` form. Missing support records a blocker
without an effectful fallback. The selected provider derives the actual cwd
project and enforces all normal finalization and native-preservation gates.
The discovery and finalization calls retain the same opaque launcher prefix;
an intervening provider rollback cannot authorize legacy finalization.
Failures, malformed replies and timeouts retain custody and record a blocker.
A lost preparation response never starts finalization or automatically repeats
the decision POST.

Successful worker output reports `workspace_closeout: finalized`, not task
release or native retirement. Independently admitted decision nudges describe
this explicit decline path, including routing preservation.
Initial prompts and version-1 same-native retry invocations remain byte-for-byte compatible with
retained invocations and independently updated peers. Decline guidance stays
outside those immutable templates; retained ordinary charters and routing
remain intact.
Initial invocation recovery also uses its original stored prompt rather than
the current generated seed. It still validates the original allocation/native
binding and requested agent/charter before reading the original receipt;
changed source guidance neither creates a replacement invocation nor rewrites
the retained assignment.
Stable initial templates also preserve keyed CLI acquisition requests and
ordinary recovery through pre-change readers during rollback.

The existing allocator supervisor consumes a pending decline before startup
retry/nudge/exhaustion logic. Only its owning supervisor, outside observe-only
mode and after existing task, action and installation-governance admission,
may request the coordinator's independently observed closeout. This client
negotiates the running coordinator's local-closeout capability and sends only
the original decline binding. Neither this supervisor nor the worker copies a
finalizer reply into release evidence. No new loop, timer, finalizer owner or
process-retirement mechanism is introduced.

## Current boundary

`DispatchClient.prepare_pool_decline` reads the running coordinator's health
contract before `POST /tasks/{task_id}/pool-decline`. It supplies version `1`,
`worker_id`, `native_id`, `generation`, `startup_revision`, `reservation_key`,
`exclusions` and `note`. This operation inherits the same coordinator data
authentication as explicit start. A pre-acceptance worker does not yet have
task-scoped ownership, and task-only credentials are not admitted to this route.

The queue rechecks its allocator-owned `CLAIMED` incarnation, completed startup,
exact retained reservation/workspace/native, execution admission, holds,
pending steer and current startup/decision deadline under its write lock.
The nonempty exclusions may name only that worker's actual `worktree:<id>`,
`pool:<registration-id>` or `machine:<execution-machine>`. Foreign, capability,
environment and repository exclusions are refused. A meaningful explanation
is mandatory. A started worker cannot reinterpret its accepted task as an
initial decline; its existing suspend/recovery contract remains separate.

The result uses `pool_worker_decline/v1`, version `1`, and a `decline` record.
That record retains the exact decision binding, sorted unique exclusions,
explanation, creation/update times and optional closeout blocker. It is also
visible in the task detail and coherent startup snapshot as `worker_decline`.
The original allocator assignment, reservation, task generation, startup
counters and task routing are unchanged. The task has **not** accepted work:
its task-row worker owner remains unset, while the decline record identifies
the exact native worker responsible for closeout.

An identical current-incarnation retry returns the original decision, even
after its original decision deadline. It does not refresh timestamps or audit
history, widen exclusions or replace the explanation. Changed identity, holds,
startup revisions or disposition refuse. Malformed or lost replies never
trigger a legacy yield, another coordinator or an automatic POST replay.
The recorded original remains available for explicit recovery.

`DispatchClient.record_pool_decline_blocker` uses the same negotiated contract
and exact binding at `POST /tasks/{task_id}/pool-decline/blocker`, adding
`detail` instead of exclusions/note. It records a failed closeout visibly
without changing the original decision. Identical blocker replay is a no-op.

While this claim is pending closeout, acceptance, startup retry/nudge/exhaustion
and reservation release/retirement cannot bypass it. Existing supervision requests independently observed closeout and retains
custody on failure rather than sending another decision nudge or falling into
legacy recovery. No new timer or cleanup owner is added.
An explicitly authorized terminal task disposition retains the historical
decline without deadlocking normal reservation retirement.

## Coordinator-owned local closeout

The control-authenticated `POST /tasks/{task_id}/pool-decline/closeout` accepts
only version `1` and the original worker/native/generation/startup-revision/
reservation binding. It accepts neither finalized JSON nor a caller-selected
project, host, path, exclusion or outcome. The coordinator derives the original
managed allocation from its durable reservation. An unknown or nonlocal
execution host, reused/targeted allocation or missing project/path refuses
without a remote or legacy fallback.

The coordinator consumes the selected Worktrees runtime's read-only
[closeout observation](../../agent-worktrees/docs/dispatch-worker-closeout.md#read-only-owner-observation).
One bounded invocation with the explicit project observes the exact
task/reservation/worktree/native. That read's own versioned response establishes
support before the queue mutation; there is no per-call preflight subprocess.
The observation must retain the reservation's allocation driver as well as its
task, attempt, supervisor and workspace. The full observation/commit operation
participates in coordinator drain accounting; new closeouts refuse while
draining, before either observation or blocker mutation.
No queue lock spans the external read. The final SQLite transaction rechecks the entire original task,
assignment, reservation and immutable decline, including holds, steering,
native identity, startup revision, routing and closeout blocker. Changed or
uncertain evidence retains the claim; endpoint failures record a visible
blocker when that same claim is still current.

Only an unchanged successful observation atomically applies the original
exclusions, advances the task generation to `QUEUED`, releases the logical
pool assignment and requests native retirement. Task-wide 1/3/6/12-hour
cooldown preserves any later scheduled due time; startup history and the
escalation stage are not reset. The original reservation remains `RELEASING`,
startup-fenced and capacity-accountable until normal exact-native retirement.
The existing startup snapshot client accepts the retained `worker_declined`
exhaustion reason, with its finite exhaustion time and original budget binding.
Unknown reasons and inconsistent exhaustion timestamps still refuse.
No process is stopped and no checkout is removed. Direct worker claiming
cannot bypass this allocator-owned history.

The response contains a durable `closeout` receipt under
`pool_worker_decline/v1`. It records the original binding, queued generation,
applied exclusions, eligibility time and owner observation. An exact repeated
request returns that historical receipt without probing again, adding audit
events or changing a later task incarnation. It is not a claim that the
current task still has that historical queued generation. After native
retirement, allocator placement can select another eligible pool while
retaining counters and the original capacity history. Optional finalized
workspaces are not carried into the next attempt; explicit/required workspace
targets remain visible, unsatisfiable constraints rather than being unpinned.
An optional replacement advances the durable allocation revision and keyed
creation identity only after the prior revision's native custody has settled.
The finalized predecessor and its creation journal remain intact; replacement
replay retains the new key rather than retrying the predecessor's creation.
An explicitly pinned old allocation revision is not silently advanced.
Closeout lookup retains the original workspace independently of optional
workspace carry, including an ordinary-Bridge-to-CLI driver transition.
Replacement is admitted before the ordinary carry-cleanup guard: native
retirement need not settle the finalized predecessor's separately accountable
pending cleanup. The replacement transaction removes only a soft worktree
affinity matching that declined workspace, preserving other affinities and
hard targets so later startup retries cannot revive stale carry.

## Retained task-claim ordering

A governed workspace keeps its own outbound task claim until Dispatch confirms
the unassignment, so finalizing first would wait on itself. When the task has a
relationship bound to the exact reservation, `decline` therefore uses this
order, advertised as `worker_decline_readiness_closeout_versions: [1]`:

1. The worker reads `GET /tasks/{task_id}/relationship` before the decision.
   A coordinator with a retained relationship but no readiness closeout support
   refuses with an upgrade error before any decision POST. Unresolved or
   foreign required relationships refuse instead of being ignored.
2. After preparation, the supervisor's closeout uses the selected runtime's
   nonfinalizing [`observe-ready`](../../agent-worktrees/docs/dispatch-worker-closeout.md#selected-runtime-readiness-transport)
   operation in place of the finalized observation. Readiness retains exactly
   that task claim and refuses any other unsettled obligation, so a requeue is
   never committed when later finalization would be blocked by unrelated
   responsibility. A refusal records a blocker and leaves the task `CLAIMED`.
3. The same SQLite transaction applies the exclusions, releases the
   assignment, clears the task's worker owner and advances the task to an
   unbound `QUEUED` generation. Its receipt records the relationship event id.
4. Only after that commit, the coordinator reconciles that exact claim through
   the existing relationship owner. Every exact closeout replay retries
   settlement against the committed receipt, and the data-authenticated
   `POST /tasks/{task_id}/pool-decline/settlement` exposes the same retry to
   the worker's own binding. Neither route can requeue again.
5. The worker waits up to ten minutes for the receipt, requires the settled
   claim, then runs normal own-worker finalization. Output reports
   `routing: requeued` and `task_relationship: closed`. A lost task read
   during the wait (transport failure, server error or invalid response) is
   unconfirmed, not a refusal: polling continues until the deadline, and the
   timeout blocker names the last failure. Confirmed client errors stop at once.

A wait timeout, refused or lost reply, or unsettled claim retains the claim
and checkout, records a visible blocker where the claim is still current, and
exits nonzero. Rerunning the same command converges: once the task is no longer
claimed at the declined generation, it settles from the committed receipt and
finalizes without a second decision or requeue. If Dispatch has already
placed the requeued task elsewhere, the current task no longer names the
original decline; the worker then reads
`GET /tasks/{task_id}/pool-decline/recovery?worker_id=&native_id=&generation=`
(advertised as `worker_decline_binding_recovery_versions: [1]`), which returns
the exact original binding and workspace only behind its committed closeout
receipt, and settles/finalizes that binding. A recovered workspace other than
the caller's own refuses. The claim is never
force-released and unrelated obligations are never waived. If another worker
reclaims the task before settlement, the old claim remains visible as retained
responsibility for the relationship owner rather than being released.

Tasks without a bound relationship keep the earlier order: finalize the own
workspace, then let the supervisor observe that finalized closeout.

## Remaining integration

Selected remote-host transport, no-managed-worktree applicability and the
complete ordinary/native matrix remain open. The retained task-claim order is
also local-only. Local source controls do not
qualify those boundaries or authorize physical cleanup.

Older or rolled-back peers missing preparation support refuse before POST;
there is no equivalent legacy fallback. A provider that ignores a retained
decline is not a supported rollback target for an adopted pending claim.
Source controls do not establish installed adoption, complete decline
closeout or end-to-end worker acceptance.
