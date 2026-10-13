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

Explicit `make_embody_spawn` and interactive CLI embodiment use
`agent-bridge acquire --request-file -`, current-client HTTP protocol 34. Existing
`spawn_backend=bridge` selects ACP; explicit `embody` selects CLI. No missing-
capability fallback changes mode or invokes worktree session launch directly.
Standalone task/script dispatch still works without bridge.

The acquisition-specific proof and receipt rules below govern the CLI and
legacy acquisition path, not ordinary ACP create/store/send. No legacy
acquisition marker implicitly grants ordinary-send authority.

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

Retries retain the recorded allocation even without an exclusive key, including
its native handle and retirement marker. Routing changes do not change that
allocation or destructive absence probes. An older coordinator lacking the
recording fields refuses before create; a recording response that ignores them
holds the reservation. Legacy carried allocations missing project/path,
incompatible owner results, reassigned or missing carried worktrees preserve
the binding for repair rather than allocate a replacement.

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
unheld task, a handle-less latest deferred/failed created-or-reused allocation,
and an exclusive-key carry chain whose latest allocation is that exact row,
no pending cleanup, no active same-task reservation and no other task's active
reservation on that worktree. It advances task generation and marks the exact
latest allocation's conversation retirement authorization, retaining original
reservation details, receipts and transcripts and writing a task audit event.
Stale/replayed authorization refuses. This only grants fresh intent; it neither
starts a worker nor proves historical absence.

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
