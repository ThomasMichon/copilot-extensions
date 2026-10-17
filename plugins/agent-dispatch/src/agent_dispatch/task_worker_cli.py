"""Pinned, task-only CLI execution for workers without fleet credentials."""

from __future__ import annotations

import argparse
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .client import DispatchClient, DispatchError
from .client_transport import validate_task_url
from .loop_commands import _resolve_cli_module

_FIELDS = {
    "task_token": "AGENT_DISPATCH_TASK_TOKEN",
    "task_scope_id": "AGENT_DISPATCH_TASK_ID",
    "task_url": "AGENT_DISPATCH_TASK_URL",
    "task_worker": "AGENT_DISPATCH_TASK_WORKER_ID",
}
_COMMANDS = frozenset({"show", "payload", "start", "progress", "heartbeat", "complete", "abandon"})


@dataclass(frozen=True)
class TaskWorker:
    token: str = field(repr=False)
    task_id: str
    url: str
    worker_id: str

    def client(self) -> DispatchClient:
        return DispatchClient(self.url, task_token=self.token)


def register_task_worker_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-token", help="task-only credential (AGENT_DISPATCH_TASK_TOKEN)")
    parser.add_argument(
        "--task-token-file", help="private renewable credential (AGENT_DISPATCH_TASK_TOKEN_FILE)",
    )
    parser.add_argument(
        "--task-scope", dest="task_scope_id",
        help="task-only identity (AGENT_DISPATCH_TASK_ID)",
    )
    parser.add_argument(
        "--task-url", help="pinned task coordinator (AGENT_DISPATCH_TASK_URL)",
    )
    parser.add_argument(
        "--task-worker", help="projected worker identity (AGENT_DISPATCH_TASK_WORKER_ID)",
    )


def task_worker(args: argparse.Namespace) -> TaskWorker | None:
    supplied = {
        name: getattr(args, name, None)
        if getattr(args, name, None) is not None else os.environ.get(env)
        for name, env in _FIELDS.items()
    }
    token_file = getattr(args, "task_token_file", None)
    if token_file is None:
        token_file = os.environ.get("AGENT_DISPATCH_TASK_TOKEN_FILE")
    if all(value is None for value in supplied.values()) and token_file is None:
        return None
    if token_file is not None:
        if supplied["task_token"] is not None:
            raise DispatchError(403, "direct and file task credentials cannot be combined")
        supplied["task_token"] = _read_task_token(token_file)
    values = {
        name: value if isinstance(value, str) else ""
        for name, value in supplied.items()
    }
    if not all(value.strip() for value in values.values()):
        raise DispatchError(403, "incomplete task-worker credential projection")
    if any(getattr(args, name, None) for name in ("token", "control_token", "shared")):
        raise DispatchError(403, "task authority cannot be combined with fleet credentials")
    url = values["task_url"]
    try:
        validate_task_url(url)
    except ValueError as exc:
        raise DispatchError(403, str(exc)) from exc
    if getattr(args, "url", None) not in (None, url):
        raise DispatchError(403, "task authority cannot override its pinned coordinator")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", values["task_scope_id"]):
        raise DispatchError(403, "invalid projected task identity")
    return TaskWorker(values["task_token"], values["task_scope_id"], url, values["task_worker"])


def _read_task_token(filename: str) -> str:
    if not isinstance(filename, str) or not filename or not Path(filename).is_absolute():
        raise DispatchError(403, "task credential file must have an absolute path")
    try:
        if not Path(filename).is_file():
            raise DispatchError(403, "task credential file must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(filename, flags), "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise DispatchError(403, "task credential file must be a regular file")
            raw = source.read(8193)
        token = raw.decode("utf-8").strip()
    except (OSError, UnicodeError, ValueError) as exc:
        raise DispatchError(403, "cannot read private task credential file") from exc
    if not token or len(raw) > 8192 or any(not 33 <= ord(char) <= 126 for char in token):
        raise DispatchError(403, "invalid private task credential file")
    return token


def run_task_worker(args: argparse.Namespace, worker: TaskWorker) -> int:
    if args.command not in _COMMANDS or getattr(args, "task_id", None) != worker.task_id:
        raise DispatchError(403, "command is outside the projected task authority")
    forbidden_options = (
        "history", "machine", "worktree", "duplicate_of", "resolve", "base", "override_live",
        "owner_session_id",
    )
    if any(getattr(args, name, None) for name in forbidden_options):
        raise DispatchError(403, "task-only commands cannot use fleet identity or history")
    if getattr(args, "worker_id", None) not in (None, worker.worker_id):
        raise DispatchError(403, "worker identity differs from the task projection")
    core = _resolve_cli_module()
    result = None
    if args.command == "complete":
        try:
            result = core._read_result(args)
        except (OSError, ValueError) as exc:
            raise DispatchError(400, f"invalid result: {exc}") from exc
    if args.command == "abandon" and not args.permit:
        raise DispatchError(403, "task abandonment requires --permit")
    with worker.client() as client:
        if args.command == "show":
            response = client.get(worker.task_id)
        elif args.command == "payload":
            response = client.payload(worker.task_id)
        elif args.command == "start":
            response = client.start(worker.task_id, worker.worker_id)
        elif args.command == "progress":
            response = client.progress(
                worker.task_id, worker.worker_id, phase=args.phase or "",
                summary=args.summary, blocker=args.blocker, pr=args.pr,
            )
        elif args.command == "heartbeat":
            response = client.heartbeat(worker.task_id, worker.worker_id)
        elif args.command == "complete":
            response = client.complete(
                worker.task_id, worker.worker_id, result_ref=args.result_ref, result=result,
            )
        else:
            response = client.abandon(
                worker.task_id, worker_id=worker.worker_id, permitted=True, reason=args.reason,
                expected_status=args.expected_status,
            )
    if args.command == "payload" and args.raw:
        if response.get("payload") is None:
            raise DispatchError(404, "task has no resolvable payload")
        print(response["payload"])
        return 0
    return core._emit(response)
