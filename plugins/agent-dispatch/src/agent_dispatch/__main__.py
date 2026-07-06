"""CLI entry point for agent-dispatch.

Two modes:
  * ``agent-dispatch serve`` runs the per-host coordinator (uvicorn).
  * every other subcommand is a thin client that talks to a coordinator
    (``--url`` / ``AGENT_DISPATCH_URL``; ``--token`` / ``AGENT_DISPATCH_TOKEN``).

Output is JSON on stdout so the CLI composes with other tooling.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from . import __version__
from .client import DispatchClient, DispatchError
from .config import Config, client_token, client_url


def _emit(value: Any) -> int:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _client(args: argparse.Namespace) -> DispatchClient:
    return DispatchClient(args.url or client_url(), token=args.token or client_token())


def _parse_affinity(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        key, _, val = item.partition("=")
        out[key.strip()] = val.strip()
    return out


def _cmd_serve(args: argparse.Namespace) -> int:
    from .config import load_config
    from .server import serve

    base = load_config()
    cfg = Config(
        host=args.host or base.host,
        port=args.port or base.port,
        db_path=args.db or base.db_path,
        token=args.token or base.token,
    )
    serve(cfg)
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    with _client(args) as c:
        task = c.create(
            args.title,
            prompt=args.prompt,
            proposed=args.proposed,
            requires=args.require or [],
            affinity=_parse_affinity(args.affinity),
            labels=args.label or [],
            payload_ref=args.payload_ref,
            payload_inline=args.payload_inline,
            target_machine=args.target_machine,
            target_worktree=args.target_worktree,
            target_repo=args.target_repo,
            source=args.source,
            origin_ref=args.origin_ref,
            dedup_key=args.dedup_key,
            not_before=args.not_before,
        )
    if args.spawn and not args.proposed:
        _spawn_worker_for(args, task)
    return _emit(task)


def _spawn_worker_for(args: argparse.Namespace, task: dict) -> None:
    """Spawn a worker via agent-bridge for a freshly created task (best effort)."""
    from . import bridge

    worker_id = f"spawn-{uuid.uuid4().hex[:8]}"
    try:
        result = bridge.spawn_worker(
            task["id"],
            agent=args.spawn_agent,
            coordinator_url=args.url or client_url(),
            worker_id=worker_id,
            wait=not args.run_async,
        )
    except bridge.BridgeUnavailable as exc:
        print(
            f"agent-dispatch: --spawn skipped ({exc}); task {task['id']} left queued "
            "for any worker to claim",
            file=sys.stderr,
        )
        return
    if result.returncode != 0:
        print(
            f"agent-dispatch: spawn via agent-bridge failed (exit {result.returncode}); "
            f"task {task['id']} remains queued. stderr: {result.stderr.strip()[:400]}",
            file=sys.stderr,
        )


def _cmd_claim(args: argparse.Namespace) -> int:
    with _client(args) as c:
        task = c.claim(
            args.worker_id,
            args.capability or [],
            task_id=args.task,
            lease_seconds=args.lease_seconds,
        )
    if task is None:
        print("no claimable task", file=sys.stderr)
        return 3
    return _emit(task)


def _simple(method: str, *arg_names: str):
    """Build a handler that forwards positional args to a client method."""

    def handler(args: argparse.Namespace) -> int:
        with _client(args) as c:
            result = getattr(c, method)(*[getattr(args, n) for n in arg_names])
        return _emit(result)

    return handler


def _cmd_yield(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(c.yield_task(args.task_id, args.worker_id, note=args.note))


def _cmd_complete(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(c.complete(args.task_id, args.worker_id, result_ref=args.result_ref))


def _cmd_abandon(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(
            c.abandon(
                args.task_id, worker_id=args.worker_id, permitted=args.permit, reason=args.reason
            )
        )


def _cmd_list(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(
            c.list(
                status=args.status,
                target_machine=args.target_machine,
                target_repo=args.target_repo,
                label=args.label,
                limit=args.limit,
            )
        )


def _cmd_find(args: argparse.Namespace) -> int:
    with _client(args) as c:
        return _emit(c.find(args.query, limit=args.limit))


def _cmd_watch(args: argparse.Namespace) -> int:
    with _client(args) as c:
        try:
            for event in c.stream_events():
                json.dump(event, sys.stdout)
                sys.stdout.write("\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-dispatch", description="Agent task queue + coordinator"
    )
    parser.add_argument("--version", action="version", version=f"agent-dispatch {__version__}")
    parser.add_argument(
        "--url", help="coordinator base URL (default: AGENT_DISPATCH_URL or config)"
    )
    parser.add_argument("--token", help="bearer token (default: AGENT_DISPATCH_TOKEN)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the per-host coordinator")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--db")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("create", help="enqueue a task")
    p.add_argument("title")
    p.add_argument("--prompt", default="")
    p.add_argument("--proposed", action="store_true", help="create as an unclaimable draft")
    p.add_argument(
        "--require", action="append", help="hard capability/identity token (repeatable)"
    )
    p.add_argument("--affinity", action="append", help="soft preference key=value (repeatable)")
    p.add_argument("--label", action="append", help="free-form label (repeatable)")
    p.add_argument("--payload-ref")
    p.add_argument("--payload-inline")
    p.add_argument("--target-machine")
    p.add_argument("--target-worktree")
    p.add_argument("--target-repo")
    p.add_argument("--source")
    p.add_argument("--origin-ref")
    p.add_argument("--dedup-key")
    p.add_argument("--not-before", type=float, default=0.0)
    p.add_argument(
        "--spawn", action="store_true",
        help="after creating, spawn a worker via agent-bridge to execute it",
    )
    p.add_argument(
        "--spawn-agent", default="task-worker",
        help="agent-bridge agent name to spawn (default: task-worker)",
    )
    p.add_argument(
        "--async", dest="run_async", action="store_true",
        help="with --spawn, don't wait for the worker (fire-and-forget)",
    )
    p.set_defaults(func=_cmd_create)

    p = sub.add_parser("approve", help="move a proposed task to queued")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("approve", "task_id"))

    p = sub.add_parser("claim", help="atomically lease one eligible task")
    p.add_argument("worker_id")
    p.add_argument("--capability", action="append", help="advertised capability (repeatable)")
    p.add_argument("--task", help="claim this specific task id (if eligible)")
    p.add_argument("--lease-seconds", type=int)
    p.set_defaults(func=_cmd_claim)

    p = sub.add_parser("start", help="mark a claimed task started")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.set_defaults(func=_simple("start", "task_id", "worker_id"))

    p = sub.add_parser("yield", help="return a held task to queued (with a note)")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.add_argument("--note")
    p.set_defaults(func=_cmd_yield)

    p = sub.add_parser("complete", help="mark a started task completed")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.add_argument("--result-ref")
    p.set_defaults(func=_cmd_complete)

    p = sub.add_parser("abandon", help="terminally abandon a task (requires --permit)")
    p.add_argument("task_id")
    p.add_argument("--worker-id")
    p.add_argument("--permit", action="store_true", help="assert abandonment is permitted")
    p.add_argument("--reason")
    p.set_defaults(func=_cmd_abandon)

    p = sub.add_parser("heartbeat", help="extend the lease on a held task")
    p.add_argument("task_id")
    p.add_argument("worker_id")
    p.set_defaults(func=_simple("heartbeat", "task_id", "worker_id"))

    p = sub.add_parser("detach", help="demote a hard worktree pin to a soft affinity")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("detach", "task_id"))

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--status")
    p.add_argument("--target-machine")
    p.add_argument("--target-repo")
    p.add_argument("--label")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("find", help="substring search over title/prompt")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=_cmd_find)

    p = sub.add_parser("show", help="show one task")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("get", "task_id"))

    p = sub.add_parser("events", help="show a task's audit trail")
    p.add_argument("task_id")
    p.set_defaults(func=_simple("events", "task_id"))

    p = sub.add_parser("recover", help="requeue expired-lease tasks")
    p.set_defaults(func=lambda args: _emit(_client(args).recover()))

    p = sub.add_parser("watch", help="stream task events (SSE) as JSON lines")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser("health", help="check coordinator health")
    p.set_defaults(func=lambda args: _emit(_client(args).health()))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DispatchError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as exc:
        print(f"agent-dispatch: cannot reach coordinator: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
