"""Historical category classification commands."""

import json
import sys


def register_category_commands(sub, *, client_factory, scope_repo) -> None:
    parser = sub.add_parser("classify-maintenance", help="preview exact historical provenance")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--origin-ref", required=True)
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preview-token")
    parser.add_argument("--actor", default="operator")
    parser.set_defaults(
        func=lambda args: classify(args, client_factory=client_factory, scope_repo=scope_repo)
    )
    inspect_parser = sub.add_parser(
        "inspect-task", help="read full task evidence on its owning machine"
    )
    inspect_parser.add_argument("task_id")
    inspect_parser.add_argument("--machine")
    inspect_parser.set_defaults(
        func=lambda args: inspect_task(args, client_factory=client_factory)
    )


def classify(args, *, client_factory, scope_repo) -> int:
    with client_factory(args) as client:
        result = client.classify_maintenance(
            repo=scope_repo(args),
            source=args.source,
            origin_ref=args.origin_ref,
            labels=args.label,
            limit=args.limit,
            apply=args.apply,
            preview_token=args.preview_token,
            actor=args.actor,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def inspect_task(args, *, client_factory) -> int:
    from . import remote_dispatch

    if remote_dispatch.is_peer_machine(args.machine):
        try:
            result = remote_dispatch.browse_remote(
                args.machine,
                [
                    "agent-dispatch",
                    "inspect-task",
                    args.task_id,
                    "--machine",
                    args.machine,
                ],
            )
        except remote_dispatch.RemoteDispatchUnavailable as exc:
            print(
                f"agent-dispatch: peer-queue browse of {args.machine!r} unavailable ({exc})",
                file=sys.stderr,
            )
            return 2

        sys.stdout.write(result.stdout or "")
        sys.stderr.write(result.stderr or "")
        return result.returncode
    with client_factory(args) as client:
        task = client.get(args.task_id)
        result = {
            "task": task,
            "events": client.events(args.task_id),
            "progress": client.progress_log(args.task_id),
            "attachments": client.attachments(args.task_id),
            "statistics": {
                key: task.get(key)
                for key in (
                    "attempts",
                    "created_at",
                    "updated_at",
                    "claimed_at",
                    "started_at",
                    "completed_at",
                )
            },
        }
        if task.get("has_result") or task.get("result_ref") or task.get("result") is not None:
            result["result"] = client.result(args.task_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
