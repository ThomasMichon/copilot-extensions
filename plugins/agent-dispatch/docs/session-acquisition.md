# Local worker session acquisition

One-shot `create --spawn`, local supervisor `make_headless_spawn`/
`make_embody_spawn`, and interactive embodiment acquire and deliver through
`agent-bridge acquire --request-file -`, current-client HTTP protocol 31. Existing
`spawn_backend=bridge` selects ACP; explicit `embody` selects CLI. No missing-
capability fallback changes mode or invokes worktree session launch directly.
Standalone task/script dispatch still works without bridge.

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
[workspace continuity effort](../../../efforts/active/workspace-acquisition-continuity/README.md)
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
clients require daemon protocol 31 and refuse a protocol-30 daemon before
sending a request, rather than assume it implements recovery revalidation.
