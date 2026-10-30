# Local worker session acquisition

One-shot `create --spawn` and local supervisor `make_headless_spawn` use ordinary
bridge create/store/send: create without a prompt, durably store the exact ready
bridge/native identity, then send under its native-conversation fence. Dispatch
binds the execution project and stable creation key before calling
`agent-bridge create --creation-key`; bridge owns both allocation and embodiment.
A lost create response replays that key rather than allocating again, and a
routing change cannot replace the recorded execution project. The created-agent
receipt retains the owner repository/worktree/path for retries and cleanup.
Older coordinators lacking keyed-allocation recording fail closed.

An already assigned ordinary worktree is reconciled through the ground owner's
version-1 exact record API in its recorded execution project. Existing
project/path bindings cannot change. When legacy metadata is absent, a
successful exact owner read must establish the candidate namespace and path
before they are bound; a failed lookup cannot make a mutable routing hint
permanent.
Owner attribution is compared with the original allocation task from retained
allocation provenance, not a dedup re-emission's new task ID. Admission still
belongs to the current task and generation.

A recorded native head selects strict worktree-scoped Bridge resume. Dispatch
uses the positional `resume <worktree-id> --strict` CLI contract with default
headless ACP selection, not a creation-only worktree selector flag. Dispatch
stores the actual resumed `SessionInfo`, including its exact native, workspace,
project and path, rather than manufacturing a fresh-created readiness receipt.
Resume failures never fall through to create. Only positively indexed empty
history without opaque/provider bindings, known native IDs or retirement may
select ordinary keyed creation in the existing workspace under the original
key; Bridge independently rechecks launch authority.

Old acquisition rows remain historical evidence, not a blanket veto or a launch
permit. Current task generation, queued/unowned state, holds, reservation release,
retirement, foreign holders and exact native identity are checked again before
the first prompt. Releasing an exclusive slot is not conversation retirement.
Explicitly retired IDs remain forbidden, but do not veto a different current,
unretired native head; unknown unretired historical identities still conflict.
Bare historical CLI handles are native IDs under their original producing
driver, even after an ordinary-bridge retry; unclassifiable bare handles refuse.
An inherited handle also retains its exact originating evidence across tasks
sharing an exclusive lane; unrelated worktree history is not imported.
A handleless inherited retirement marker permits only current-head inspection
when all known retired identities are attributable. Exact resumed-agent storage
can atomically clear that current derived marker for a different unretired
head; historical retirement evidence is unchanged. That derived marker alone
never permits creation. A separate explicit replacement grant is described below.
Assigned keyed reservations missing project/path metadata defer through normal
supervisor retry without probing or launching a native session.
Reconciliation preserves explicit prior workspace ownership;
a fresh ordinary Bridge allocation remains Bridge-owned and `targeted` from
Dispatch's perspective. Deploy compatible exact-record, strict-resume and
coordinator facts/storage support together; older capabilities refuse explicitly.

## Authorized replacement in the same workspace

When the recorded conversation has stopped and must not be resumed, an operator
can authorize one replacement while keeping the task, workspace and old history.
This is separate from ordinary resume and from the legacy `authorize-fresh`
acquisition path:

```text
agent-dispatch reservations authorize-replacement <task-id> \
  --reservation-key <latest-deferred-reservation> --worktree <worktree-id> \
  --expected-generation <generation> --expected-head <old-native-uuid> \
  --expected-head-revision <head-revision> \
  --expected-lifecycle-revision <lifecycle-revision> \
  --permit --confirm-no-live-holder --reason "<authorization and evidence>"
```

The command requires the coordinator's control credential. Worker credentials
cannot grant replacement. It reads the exact workspace record itself, requires
Bridge payload and running daemon protocol 52, and invokes Worktrees'
[read-only stopped-head check](../../agent-worktrees/docs/conditional-head-replacement.md).
An older or unavailable receiver refuses before changing queue state.
The request does not accept a caller-supplied workspace record.

Under the queue write transaction, the grant requires the exact task generation
and latest deferred/failed unbound reservation, retained original allocation
provenance, no task hold or owner, and no active reservation or pending cleanup
on that workspace. Targeted workspace affinity must still match. It advances
the task generation, records a stable `replacement_creation_key`, and retains
all old reservation details and native retirement evidence. Repeating a stale
authorization refuses; it does not produce a second key. The response's
`launched: false` means no conversation has been started.

The next admitted reservation exposes the current grant through
`native_replacement_authorization`. Dispatch uses the grant's stable key and
`create --worktree-id <id> --replacement-task <task-id>`, without a prompt.
Bridge independently reads the authenticated `dispatch:` namespace, rechecks
permission and stopped holders before launch, then uses the ground owner's
conditional head replacement. It never falls back to ordinary bind or resume.
The original workspace allocation key is unchanged; the replacement key
identifies this new conversation only.

Before sending work, Dispatch stores the new identity and frozen first prompt.
Replacement storage requires the exact grant, Bridge's completed replacement
result, matching workspace nonce, and the ground owner's new native head and
revision. The HTTP storage route obtains that record itself; caller-supplied
metadata cannot clear retirement. Only the new reservation's derived retirement
marker is cleared. Historical rows and retired native IDs remain unchanged.
A storage or send failure retains the same task and keys for retry.

After identity storage, ordinary retained-handle delivery owns retries.
Direct creation replay is supported while the grant still authorizes the
unbound reservation; it is not a second permission to recreate a stored or
retired result. Roll out all three compatible receivers before authorizing
this path. No service restart, rollout, teardown, claim transfer or task
completion is authorized by the grant.

Explicit `make_embody_spawn` and interactive CLI embodiment use
`agent-bridge acquire --request-file -`, current-client HTTP protocol 34. Existing
`spawn_backend=bridge` selects ACP; explicit `embody` selects CLI. No missing-
capability fallback changes mode or invokes worktree session launch directly.
Standalone task/script dispatch still works without bridge.

The acquisition-specific proof and receipt rules below govern the CLI and
legacy acquisition path, not ordinary ACP create/store/send. No legacy
acquisition marker implicitly grants ordinary-send authority.

Bridge also exposes venue-backed `create --cli --detach` for an observable
muxed session. That command delegates to the venue's CLI launcher; it is not
the keyed local workspace-creation contract. The current Bridge CLI explicitly
rejects combining `--cli` with `--creation-key`. Keyed local workspace creation
can retain an initial prompt as part of its original request, but Dispatch's
ordinary path above deliberately sends only after storing the created-agent
facts. Neither capability alone proves retry-safe first-prompt delivery for
detached CLI creation. A convergence change must qualify that combination,
including pending creation and response loss, before replacing these paths.
It must also distinguish process/mux startup from native session birth:
waiting for first-prompt-dependent session or extension registration before
submitting the seed would create a circular readiness dependency. Missing
registration after submission does not establish nonacceptance.
See [Bridge's creation contracts](../../agent-bridge/docs/session-acquisition.md)
and the [spawn diagnostic boundaries](spawn-supervisor.md#allocation-progress-pool-selection-and-diagnostics).

Allocation, reservation recording, task claim/adoption, evaluation, pending
steers and terminal cleanup keep their original owners. Dispatch conveys
current generation/reservation, recorded worktree provenance, explicit
retirement (including unknown), carried identity, charter, project, driver and
verification timeout. The `dispatch:` namespace exposes current owner facts
and the latest acquisition reservation for bridge verification.
For a reserving reused allocation with 2–256 attempts, the provider also supplies
a versioned bounded reservation-history snapshot. Bridge requires the complete
consecutive same-task chain and a request-bound `refused/prepared` receipt for
every predecessor, starting with a created allocation, before creating a first
conversation in the reused directory. A missing receipt, truncated/gapped
history, prior native handle, spawned outcome or unknown launch never grants
fresh authority. Bridge revalidates that proof after target preparation and
before entering its native launch gate. Older providers omit the snapshot and
retain strict refusal; no caller-supplied history grants permission.
Only an active task (`queued`, `claimed`, `started`, or `suspended`) with a
`reserving`/`spawned` reservation that has not requested release may use that
reservation as its namespace worktree fallback. Historical reservations remain
visible as evidence but cannot redirect a completed task away from its durable
session.

Local allocation persists the ground owner's repository, worktree ID, and path
as `allocation_project`, `worktree`, and `worktree_path` before acquisition.
Request version 2 carries that same project/path; bridge checks both the
reservation and exact owner record. ACP selects its registered worker project;
the default CLI worker needs no ACP registration. The queue lane still owns
claim authority and does not qualify the execution workspace.

The `dispatch:` namespace also publishes that execution identity as the
worktree reference's `project`, but only from a current active reservation bound
to the same worktree. It never substitutes the producing lane or borrows a
historical/released allocation. A malformed non-null project refuses the
provider response. Bridge validates and preserves the identity, then queries
the actual workspace owner for fresh hosting admission before readiness or
plugin staging; the provider does not supply an authoritative launch directory.

Retries retain the recorded allocation even without an exclusive key, including
its native handle and retirement marker. Routing changes do not change that
allocation or destructive absence probes. An older coordinator lacking the
recording fields refuses before create; a recording response that ignores them
holds the reservation. On the legacy acquire path, carried allocations missing project/path,
incompatible owner results, reassigned or missing carried worktrees preserve
the binding for repair rather than allocate a replacement. Ordinary assigned
worktrees use the current-owner reconciliation above without changing known
bindings or replacing native history.

Before the first create, the coordinator also binds the selected execution
project through the reservation's `/allocation` operation. Even a lost ground
response therefore cannot restore mutable routing as target authority.
`creation_key` is derived from the durable queue identity, canonical queue lane,
typed deduplication identity (task ID when absent), and persisted
`allocation_revision`. Attempts, task generation and transport retries do not
advance that revision. The owner receives `create --creation-key` and the
original allocation task ID and `allocation_reservation_key`/attempt provenance
on every replay. A re-emitted task with the same typed deduplication identity
retains that ground provenance, even before its original response was recorded;
the current task/reservation still owns admission and prompt delivery.

`create --spawn --allocation-revision <positive-integer>` is the explicit
replacement input; the reserve API/client expose the same optional field.
Explicit revision requests use `/spawn-reservations/revisions`, so an older
coordinator cannot silently ignore the field through the legacy reservation
endpoint. A missing capability refuses before reservation or launch.
Omitting it retains the previous revision (initially 1). Advancing it requires
positive `removed`/`already-removed` disposal evidence for the prior allocation
and no active attempt. Primed, held, pending and unknown outcomes are not
disposal. Pending cleanup preserves the same logical identity and refuses its
next reservation rather than clearing the workspace. A different logical
request may reserve its own key, but cannot inherit or rebind that cleaning
allocation. Terminal key replay cannot allocate again.
The original allocation reservation qualifies destructive ground cleanup;
the current spawn reservation still owns admission and prompt delivery.

An exact deterministic `refused/prepared` receipt may attest `not_started` for
the current request and spend the bounded failure budget. Unavailable-owner,
busy, pending, prior native identity and historical launch outcomes retain
reconciliation. A local acquisition failure preserves its allocation rather
than concluding and recreating it as a retry strategy.
The ground owner's typed exit-3 `creation.status=pending` outcome is deferred
under the same creation key, including one-shot and supervised CLI preparation.
It does not spend the failed-spawn budget. Conflict, terminal and malformed
responses are not silently reclassified as pending.

Prompt/delivery identity is stable per reservation, not a random worker ID on
each retry. Bridge—not dispatch—selects reuse/resume/create. A task generation
increment on claim/adoption does not itself retire its transcript. A carried
handle from another task/exclusive occurrence does not prove transcript affinity.
Missing native/retirement proof refuses rather than starting blank.

CLI success returns its exact native session/worktree cleanup identities.
ACP retains `local-body:<bridge-id>` and separately returns native conversation
identity. Missing bridge/native-output proof and uncertain launch effects never
become “body absent”; ambiguous outcomes defer for reconciliation. Interactive
uncertain launch preserves its worktree claim instead of releasing it beneath
a possible running process.

Fleet/remote acquisition and legacy helper retirement are still Phase 6 work.
CLI charter/model/effort overrides are capability-refused until effective-state
verification is implemented. Existing configured defaults are not replaced
when an acquisition request has no override.

ACP observation (`wait`) is separate from acquisition. A failed/timed-out
observation reports `observation_error` and a warning while retaining successful
acquisition status, bridge/native identities, and the cleanup handle; it never
turns an existing body into a spawn failure. Protocol-29 pre-request capability
refusal is a proven unsupported capability, not an ambiguous launch.
An old bridge executable's exact argparse exit-2 rejection of the `acquire`
command (empty stdout, bridge usage and command diagnostic) also proves no
request was issued. It fails capability without deferred reconciliation; other
non-JSON errors remain ambiguous.

Bridge-delivered prompts supply the verified native conversation to
`start --owner-session-id <native-id>`; the CLI forwards it into the existing
start admission field. New explicit acquisition workers use a stable
`acquired-<reservation>` identity and cannot enter STARTED without that native
binding. Claim/start clears prior activity instead of inheriting an earlier
turn's IDLE. Local supervisor activity samples carry their observation time;
samples taken before the current claim/start are ignored. An explicitly
non-resting owned RUNNING turn wins over lingering idle metadata.
Start authority is not inferred from a worker-name prefix. Before prompt
delivery, bridge binds the acquisition receipt through the registered dispatch
provider. The coordinator independently reads the authenticated bridge's durable
`acquisition-inspect` result and compares the complete task/native/session/mode
binding before recording it. STARTED admission checks that receipt atomically
in the task transition transaction for both explicit workers and CLI worktree
owners; missing or different native IDs refuse, including idempotent replay.
CLI native-handle activity probes, as well as local ACP probes, timestamp their
snapshot before the liveness query. A delayed pre-start IDLE response cannot
restore stale task activity after start.
Current local probes also carry the coordinator-issued generation/status/start/
owner snapshot; that tuple, not cross-host clock comparison, fences admission.
Legacy callers without the tuple retain the historical timestamp fallback.
Start checks that bridge delivery has actually reached admission, not just that
a pre-delivery receipt was prepared: authenticated inspection must carry
`delivery_admitted: true` from the exact durable inbox/turn row, including its
session, delivery key and prompt. A `delivering` stage alone cannot authorize
start. Unsupported CLI overrides fail as proven `not_started` capability
mismatches rather than being deferred and repeatedly retried.
Post-launch task-fence refusal defers;
it never authorizes created-worktree teardown as a pre-effect failure.

## Explicit operator fresh-conversation recovery

The cross-layer
[workspace allocation and session acquisition contract](../../../docs/patterns/workspace-allocation-and-session-acquisition.md)
distinguishes queue lane, selected execution project, and code target. The
workspace owner-returned execution identity must be preserved across
preparation, bridge acquisition, recovery, and cleanup. The
[workspace continuity effort](../../../efforts/2026/10/13%20workspace-acquisition-continuity/README.md)
tracks correction of independent project derivation and adds stable creation
identity across attempts. This is not authorization to weaken the historical
first-conversation or fresh-recovery checks below.

When historical initial-launch evidence is missing, no automated absence
inference is permitted. After independently verifying that the exact worktree
has no active or unknown native holder, an operator may authorize a new
conversation instead of claiming the old launch never happened:

```text
agent-dispatch reservations authorize-fresh <task-id> \
  --reservation-key <latest-concluded-reservation> --worktree <worktree-id> \
  --expected-generation <generation> --permit --confirm-no-live-holder \
  --reason "<operator authorization and verified liveness evidence>"
```

The client uses its configured control identity, not the worker credential.
The coordinator atomically requires the exact generation, queued/unowned/
unheld task, a handle-less latest deferred/failed created, reused or targeted
allocation, and an exclusive-key carry chain whose latest allocation is that exact row,
no pending cleanup, no active same-task reservation and no other task's active
reservation on that worktree. It advances task generation and marks the exact
latest allocation's conversation retirement authorization, retaining original
reservation details, receipts and transcripts and writing a task audit event.
Stale/replayed authorization refuses. This only grants fresh intent; it neither
starts a worker nor proves historical absence.

For a targeted allocation, the task's current worktree affinity and any explicit
worktree target must still name the exact retained worktree. Authorization
preserves the original targeted ownership and allocation project/path; it does
not turn an externally supplied worktree into a dispatch-created resource or
grant teardown permission. The next ordinary reservation reuses that same
allocation and carries the fresh-conversation authorization. Do not replace the
task, change its target or manufacture an initial-launch receipt to recover it.

The next reservation carries this retirement authorization through the existing
owner-verified acquisition boundary. Bridge still independently refuses live
or unknown holders and native heads, revalidates task/reservation/retirement
after preparation, and applies existing launch/executor fences. A holder racing
the operator's check blocks launch; permission never authorizes stopping it.
An active reservation must finish its normal defer/conclusion before this verb;
never force-clear it or directly edit the database. Deploy the promoted
bridge and dispatch fixes through normal immutable-runtime activation first.
The promoted bridge payload/client must be deployed too: current acquisition
clients require daemon protocol 34 and refuse an older daemon before
sending a request, rather than assume it implements recovery revalidation.
