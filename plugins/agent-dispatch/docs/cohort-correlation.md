# Inspecting original Dispatch resource correlation

The existing owner surfaces expose original provenance separately from current
assignment. This is inspection, not a cleanup operation or proof of native
absence. Use the original coordinator and the selected Worktrees/Bridge owners;
matching a raw identifier on another coordinator is not an authoritative join.

## Forward and reverse reads

| Starting point | Existing owner surface | Meaning |
|---|---|---|
| Task | `GET /tasks/{task_id}/attachments` | Every recorded native/worktree attachment, including detached history |
| Native session | `GET /sessions/{session_id}/tasks` | Reverse attachment history; a historical match is not current ownership |
| Reservation | `GET /spawn-reservations/{key}` | Original allocation and session-origin candidates, selected carriers and retained body references |
| Protected assignment | Task detail `task_obligation.relationship`, optionally selected with `obligation_assignment_event_id` | Original queue, assignment event, workspace, worker and assignment cohort |
| Managed workspace | Worktrees `record --worktree-id <id> --json` | Exact project-scoped record, original `dispatch_attempt`, creation nonce and current session/head state |
| ACP session | Bridge session list/detail | Validated original `dispatch_origin` from first-save config, including stopped sessions |
| CLI session | Bridge live-session list/detail | Original `dispatch_origin`; list with `include_dead=true` to include retained exited sessions |

Dispatch's session routes above are on the coordinator. Bridge's distinct
session routes use `/api/v1/sessions` and `/api/v1/live-sessions`.
An empty result describes only that owner's retained data, not another owner's
resources or independent proof that a process is gone.

## Keep the three origins distinct

Worktrees' `dispatch_attempt` identifies workspace birth. Its `cohort` retains
the allocation's original emitter receipt and exact pool assignment. Resolve
that original reservation, not the current task's latest reservation.

Bridge's `dispatch_origin` identifies session birth. Its original task and
reservation may differ from the workspace's allocation after reuse. Conversely,
a resumed conversation keeps its old origin or honest legacy absence; a new
session candidate on a reservation does not prove that session was created.

The protected assignment audit identifies a task/workspace relationship. Use
its original queue identity and assignment event when inspecting that
relationship. It neither replaces workspace birth nor changes a conversation's
origin when another task attaches.

Compare complete pool references: registration ID, machine, environment,
catalog revision, task generation and reservation key. A display name or the
current catalog is not the original assignment. Emitter references come from
managed creation receipts, not source labels. Null, absent, opaque or unsupported
metadata stays unattributed; do not backfill it from a later assignment.
The [obligation contract](task-obligations.md) owns these field definitions.

## Evidence boundary

Contained source controls exercise reassignment, retained detached natives,
original pool references, exact workspace reads, restart and stopped/expired
session discovery. Synthetic retirement evidence belongs only to those
fixtures. Inspection never authorizes native termination, worktree deletion,
task abandonment or release of foreign obligations.

Actual native conclusion, workspace disposition, recovered-owner closeout and
installed-runtime qualification remain separate gates in the
[active effort](../../../efforts/active/task-relevance-reconciliation/README.md).
The [Bridge origin contract](../../agent-bridge/docs/dispatch-session-origin.md)
owns first-save, reuse and compatibility semantics.
