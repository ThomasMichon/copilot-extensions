"""Parser registration for task lifecycle/admin CLI families."""

from __future__ import annotations

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def register_task_lifecycle_commands(sub) -> None:
    p = sub.add_parser(
        "claim", help="atomically lease one eligible task (identity auto-resolved from CWD)"
    )
    p.add_argument(
        "task_id",
        nargs="?",
        help="claim THIS specific task id (optional; default: any eligible task). "
        "First-positional task id, consistent with start/complete/yield/abandon.",
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.add_argument(
        "--worker",
        "--as",
        dest="worker_id",
        help="explicit owner/worker id to claim as (rarely needed; default: "
        "composed from machine/worktree). Was the bare positional, now a flag "
        "so it can't be confused with the task id.",
    )
    p.add_argument("--capability", action="append", help="advertised capability (repeatable)")
    p.add_argument(
        "--task",
        help="alias for the positional task id (back-compat)",
    )
    claim_scope = p.add_mutually_exclusive_group()
    claim_scope.add_argument(
        "--repo",
        help="lane to claim from (local name or remote URL). Default: the calling "
        "repo. A worker only claims tasks in its own repo's lane.",
    )
    claim_scope.add_argument(
        "--all-repos",
        action="store_true",
        help="administrative mode: claim across every repo lane explicitly",
    )
    p.add_argument("--lease-seconds", type=int)
    p.add_argument(
        "--evaluation",
        action="store_true",
        help="claim under the tight EVALUATION lease (a quick accept/reject "
        "window): a stuck evaluator auto-releases fast, and 'start' then "
        "extends to the full work lease on commit. Decline with "
        "'yield --exclude-self' or 'abandon --duplicate-of'.",
    )
    p.set_defaults(func=_core()._cmd_claim)

    p = sub.add_parser(
        "worktree-status",
        help="this worktree's inbox: tasks assigned to + owned by it (identity auto-resolved)",
    )
    p.add_argument("--machine", help="override the resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.add_argument(
        "--repo",
        help="lane to scope the inbox to (local name or remote URL). Default: the calling repo.",
    )
    p.set_defaults(func=_core()._cmd_worktree_status)

    p = sub.add_parser(
        "claim-status",
        help="claim-provider callback (claim-provider-pattern effort): does "
        "task TASK_ID exist, for agent-worktrees' 'dispatch-task:' claim "
        "provider registry entry -- not a human-facing command",
    )
    p.add_argument("task_id")
    p.set_defaults(func=_core()._cmd_claim_status)

    p = sub.add_parser(
        "start", help="mark a claimed task started (identity auto-resolved from CWD)"
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_core()._cmd_start)

    p = sub.add_parser(
        "suspend",
        help="park a started task as dormant while retaining its owner",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    p.add_argument(
        "--reason",
        required=True,
        help="required meaningful reason recorded in the task audit trail",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.set_defaults(func=_core()._cmd_suspend)

    p = sub.add_parser(
        "resume",
        help="resume a suspended task under the same owner and wake it",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    p.add_argument("--message", help="override the wake nudge text")
    p.add_argument(
        "--no-wake",
        dest="wake",
        action="store_false",
        help="resume the lifecycle without sending an agent-bridge wake nudge",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.set_defaults(func=_core()._cmd_resume, wake=True)

    p = sub.add_parser(
        "release",
        help="release a suspended task to queued for replacement embodiment",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: composed from machine/worktree)",
    )
    p.add_argument("--reason", help="optional release note for the audit trail")
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.set_defaults(func=_core()._cmd_release)

    p = sub.add_parser(
        "yield",
        help="return a held task to queued (with a note; identity auto-resolved)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument("--note")
    p.add_argument(
        "--exclude-self",
        "--not-me",
        choices=("worktree", "machine"),
        dest="exclude_self",
        help="append a scoped self-EXCLUSION when yielding, so this same "
        "candidate isn't re-offered the task: 'worktree' (narrowest -- this "
        "worktree only) or 'machine' (this whole machine). Prefer the "
        "narrowest scope that is true. (`--not-me` is a deprecated alias.)",
    )
    p.add_argument(
        "--exclude",
        help="append an explicit exclusion token when yielding (e.g. "
        "'agent:reviewer'); overrides --exclude-self.",
    )
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_core()._cmd_yield)

    p = sub.add_parser(
        "complete",
        help="mark a started or suspended task completed under its owner",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id",
        nargs="?",
        help="owner id (default: the machine/worktree resolved from CWD, so a "
        "worker can `complete <id>` without typing its own owner)",
    )
    p.add_argument("--machine", help="override the resolved machine identity")
    p.add_argument("--worktree", help="override the resolved worktree identity")
    p.add_argument("--result-ref")
    result_group = p.add_mutually_exclusive_group()
    result_group.add_argument(
        "--result-json",
        help="structured completion result as JSON",
    )
    result_group.add_argument(
        "--result-file",
        metavar="PATH",
        help="read the structured completion result from a JSON file; '-' reads stdin",
    )
    p.set_defaults(func=_core()._cmd_complete)

    p = sub.add_parser(
        "abandon",
        help="terminally abandon a task (requires --permit or --duplicate-of)",
    )
    p.add_argument("task_id")
    p.add_argument("--worker-id")
    p.add_argument("--permit", action="store_true", help="assert abandonment is permitted")
    p.add_argument("--reason")
    p.add_argument(
        "--duplicate-of",
        dest="duplicate_of",
        metavar="REF",
        help="retire the task as a DUPLICATE of REF (an existing task id, PR, or "
        "issue). Self-justifying: implies --permit and records the dedup "
        "reference in the reason, so the decision is never a silent drop.",
    )
    p.add_argument(
        "--resolve",
        action="store_true",
        help="also emit the drive-the-worktree-to-resolution plan (the unwind the "
        "worker must run on its own worktree). Advisory -- runs nothing.",
    )
    p.add_argument(
        "--base",
        metavar="BRANCH",
        help="with --resolve, the base branch the worktree unwinds onto "
        "(default: the branch's tracked upstream)",
    )
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    from . import reattach as _reattach
    _reattach.add_abandon_override_live_argument(p)
    p.set_defaults(func=_core()._cmd_abandon)

    p = _reattach.build_reattach_subparser(sub)
    p.set_defaults(func=_core()._cmd_reattach)

    p = sub.add_parser(
        "pause",
        help="set a durable operator hold on a task (Phase 1's Pause primitive)",
    )
    p.add_argument("task_id")
    p.add_argument("--reason", required=True, help="required reason recorded in the audit trail")
    p.add_argument("--actor", help="operator identity recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    p.set_defaults(func=_core()._cmd_pause)

    p = sub.add_parser(
        "unpause",
        help="clear a hold set by `pause`",
    )
    p.add_argument("task_id")
    p.add_argument("--actor", help="operator identity recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row) -- ignored "
        "when the task is already unheld (a harmless no-op)",
    )
    p.set_defaults(func=_core()._cmd_unpause)

    p = sub.add_parser(
        "embody",
        help="Phase 1's interactive-embodiment transaction: open a task into "
        "an interactive CLI-backed session (currently requires --interactive)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--interactive",
        action="store_true",
        required=True,
        help="run the interactive-embodiment transaction (the only mode "
        "implemented so far -- an unattended/autopilot `embody` equivalent "
        "already exists via the supervisor's own spawn path, not this verb)",
    )
    p.add_argument(
        "--machine",
        help="override the resolved machine identity (default: this host, "
        "via agent-worktrees)",
    )
    p.add_argument(
        "--project",
        help="target project name (default: resolved from the task's repo)",
    )
    p.set_defaults(func=_core()._cmd_embody_interactive)

    p = sub.add_parser(
        "force-stop",
        help="terminate a started task's exact current session and park it "
        "suspended, without a durable pause hold",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--machine",
        help="this machine's identity, for deciding whether the task's "
        "session is local or on a fleet host (default: resolved via "
        "agent-worktrees)",
    )
    p.add_argument("--actor", help="operator identity recorded in the audit trail")
    p.set_defaults(func=_core()._cmd_force_stop)

    p = sub.add_parser(
        "reset",
        help="Phase 2's gentler 'not like this': discard the current attempt's "
        "embodiment state and re-admit the task for a fresh attempt",
    )
    p.add_argument("task_id")
    p.add_argument(
        "--to",
        default="proposed",
        help="target state (only 'proposed' is implemented today)",
    )
    p.add_argument("--reason", help="optional note recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    p.set_defaults(func=_core()._cmd_reset)

    p = sub.add_parser(
        "confirm",
        help="the Completion Review card's Confirm action: corroborate a "
        "completion claim and close the task for good (completed -> confirmed)",
    )
    p.add_argument("task_id")
    p.add_argument("--actor", help="operator/evaluator identity recorded in the audit trail")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    p.set_defaults(func=_core()._cmd_confirm)

    p = sub.add_parser(
        "reopen",
        help="the Completion Review card's Re-queue-with-steering action: "
        "return a completed-but-unconfirmed task to queued, progress "
        "preserved (completed -> queued)",
    )
    p.add_argument("task_id")
    p.add_argument("--reason", help="optional note recorded in the audit trail")
    p.add_argument(
        "--field",
        action="append",
        metavar="KEY=VALUE",
        help="an operator steer field to attach atomically with the reopen "
        "(repeatable); omit for a plain reopen with no new instructions",
    )
    p.add_argument("--sender", help="operator identity recorded on the attached steer, if any")
    p.add_argument(
        "--expected-status",
        dest="expected_status",
        help="reject with 'task changed; refresh and retry' if the task's "
        "current status doesn't match this (a stale cached row)",
    )
    p.set_defaults(func=_core()._cmd_reopen)

    p = sub.add_parser("heartbeat", help="extend the lease on a held task")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.set_defaults(func=_core()._simple("heartbeat", "task_id", "worker_id"))

    p = sub.add_parser(
        "progress",
        help="record a brief progress beat toward the goal (also heartbeats the "
        "lease; identity auto-resolved from CWD)",
    )
    p.add_argument("task_id")
    p.add_argument(
        "worker_id", nargs="?", help="owner id (default: composed from machine/worktree)"
    )
    p.add_argument(
        "--phase",
        default="",
        help="short phase label (e.g. 'planning', 'implementing', 'PR open')",
    )
    p.add_argument(
        "--summary",
        required=True,
        help="one-line status toward the goal (hard-capped; keep it a line, not a transcript)",
    )
    p.add_argument("--blocker", help="a real blocker holding progress, if any")
    p.add_argument("--pr", help="the PR/ref this beat corresponds to, if any")
    p.add_argument("--machine", help="override the resolved machine (targeting identity)")
    p.add_argument("--worktree", help="override the resolved worktree id (targeting identity)")
    p.set_defaults(func=_core()._cmd_progress)

    p = sub.add_parser(
        "focus",
        help="set/show this worktree's current focus (its status-core summary "
        "on the worktree record); identity auto-resolved from CWD",
    )
    p.add_argument(
        "focus_text",
        nargs="?",
        help="one-line focus for this worktree; omit to show the current focus",
    )
    p.add_argument("--list", action="store_true", help="list every worktree's focus")
    p.add_argument("--machine", help="filter --list to a machine / override resolved machine")
    p.add_argument("--worktree", help="override the resolved worktree id")
    p.set_defaults(func=_core()._cmd_focus)

    p = sub.add_parser("detach", help="demote a hard worktree pin to a soft affinity")
    p.add_argument("task_id")
    p.set_defaults(func=_core()._simple("detach", "task_id"))

