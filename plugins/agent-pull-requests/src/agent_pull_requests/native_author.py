"""Capability-directed authorship forwarding; no alternate forge transport."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .native_command import run_command


def add_parsers(subparsers, *, repo_type, handler):
    from .native_cli import validate_selector

    for name in ("author-capabilities", "update", "push", "ready"):
        author = subparsers.add_parser(name, help={
            "author-capabilities": "read target-qualified native author capabilities",
            "update": "repair existing native PR metadata without pushing",
            "push": "publish a pinned admitted source workspace through Worktrees",
            "ready": "move an explicit native draft PR to ready without consent or hold release",
        }[name], allow_abbrev=False)
        author.add_argument("--repo", required=True, type=repo_type)
        author.add_argument("--worktrees-command", required=True, type=validate_selector)
        author.add_argument("--config", help="Explicit delivered worker policy (native only)")
        author.add_argument("--json", action="store_true")
        author.set_defaults(handler=handler)
        if name == "author-capabilities":
            continue
        if name == "ready":
            author.add_argument("--number", required=True, type=int)
            continue
        author.add_argument("--publication-origin")
        author.add_argument("--publication-label", action="append", default=[])
        if name == "update":
            author.add_argument("--number", required=True, type=int)
            author.add_argument("--title")
            body = author.add_mutually_exclusive_group()
            body.add_argument("--body")
            body.add_argument("--body-file")
            author.add_argument("--reviewer", action="append", default=[])
            author.add_argument("--work-item", action="append", default=[])
        else:
            author.add_argument("--workspace", required=True)
            author.add_argument("--head", required=True)
            author.add_argument("--expected-head", required=True)
            author.add_argument("--expected-remote-head", default=None)


def config_options(args) -> list[str]:
    path = getattr(args, "config", None)
    if path is None:
        return []
    if not path or not Path(path).is_file():
        raise ValueError("Explicit native author configuration file is unavailable")
    return [f"--config={Path(path).resolve()}"]


def capability(args, prefix: list[str], operation: str | None = None) -> dict:
    options = config_options(args)
    response = run_command(
        [*prefix, "pr-author-capabilities", f"--repo={args.repo}", "--json", *options], timeout=20.0,
    )
    try:
        body = json.loads(response.stdout)
    except (ValueError, TypeError):
        body = None
    if (
        response.returncode or not isinstance(body, dict)
        or body.get("schema") != "agent-worktrees.pr-author-capabilities"
        or type(body.get("version")) is not int or body["version"] != 1
        or body.get("repo") != args.repo or not isinstance(body.get("operations"), dict)
        or operation is not None and body["operations"].get(operation) is not True
        or options and body["operations"].get("worker-policy-config") is not True
    ):
        raise ValueError(
            "Selected Worktrees runtime does not advertise this target-qualified author operation; "
            "no forge fallback or runtime update was attempted"
        )
    return body


def create_options(args, prefix: list[str]) -> list[str]:
    workspace = getattr(args, "source_workspace", None)
    expected = getattr(args, "expected_head", None)
    if bool(workspace) != bool(expected):
        raise ValueError("--source-workspace and --expected-head must be supplied together")
    if not workspace:
        if getattr(args, "body_file", None) or config_options(args):
            capability(args, prefix, "qualified-create")
        return config_options(args)
    capability(args, prefix, "qualified-create")
    return [f"--source-workspace={workspace}", f"--expected-head={expected}", *config_options(args)]


def run(args, prefix: list[str]) -> tuple[int, dict]:
    if args.command == "author-capabilities":
        return 0, {**capability(args, prefix), "frontend": {"worker-policy-config": True}}
    if args.command == "ready" and args.number < 1:
        raise ValueError("--number must be positive")
    operation = {"update": "metadata-update", "push": "source-push", "ready": "draft-ready"}[args.command]
    support = capability(args, prefix, operation)
    command = {"update": "pr-update", "push": "pr-push", "ready": "pr-author-ready"}[args.command]
    argv = [command, f"--repo={args.repo}", "--json"]
    argv.extend(config_options(args))
    if args.command == "update":
        if args.number < 1:
            raise ValueError("--number must be positive")
        argv.append(f"--number={args.number}")
        if args.title is not None:
            argv.append(f"--title={args.title}")
        if args.body_file:
            argv.append(f"--body-file={args.body_file}")
        elif args.body is not None:
            argv.append(f"--body={args.body}")
        for value in args.reviewer:
            argv.append(f"--reviewer={value}")
        for value in args.work_item:
            argv.append(f"--work-item={value}")
    elif args.command == "ready":
        argv.append(f"--number={args.number}")
    else:
        argv.extend((
            f"--workspace={args.workspace}", f"--branch={args.head}",
            f"--expected-head={args.expected_head}",
        ))
        if args.expected_remote_head is not None:
            argv.append(f"--expected-remote-head={args.expected_remote_head}")
    if getattr(args, "publication_origin", None):
        argv.append(f"--publication-origin={args.publication_origin}")
    argv.extend(f"--publication-label={label}" for label in getattr(args, "publication_label", ()))
    try:
        result = run_command([*prefix, *argv], timeout=90.0)
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        raise RuntimeError(
            "Native author response is unconfirmed; reconcile the same source/PR before retrying"
        ) from None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError(
            "Native author response is unconfirmed; reconcile the same source/PR before retrying"
        ) from None
    if (args.command != "ready" and isinstance(payload, dict) and result.returncode
            and payload.get("error") and "repo" not in payload):
        return result.returncode, payload
    if not isinstance(payload, dict) or payload.get("repo") != args.repo:
        raise RuntimeError("Native author response does not identify the selected target; reconcile before retrying")
    if args.command in ("update", "ready") and (
        type(payload.get("number")) is not int or payload["number"] != args.number
    ):
        raise RuntimeError("Native author response does not identify the selected PR; reconcile before retrying")
    if args.command == "ready":
        action = payload.get("actions")
        draft = action.get("draft") if isinstance(action, dict) else None
        status = draft.get("status") if isinstance(draft, dict) else None
        if (
            payload.get("schema") != "agent-worktrees.pr-ready"
            or type(payload.get("version")) is not int or payload["version"] != 1
            or not isinstance(support.get("provider"), str) or not support["provider"]
            or payload.get("provider") != support["provider"]
            or status not in ("not-attempted", "confirmed", "failed", "unknown")
            or type(payload.get("ok")) is not bool
            or payload["ok"] != (status == "confirmed")
            or payload["ok"] and result.returncode
            or not payload["ok"] and (
                not isinstance(payload.get("error"), str) or not payload["error"].strip()
            )
            or payload["ok"] and payload.get("error")
        ):
            raise RuntimeError("Native readiness response is unconfirmed; reconcile this PR before retrying")
    if result.returncode or payload.get("ok") is not True:
        return result.returncode or 1, payload
    if args.command == "push" and (
        payload.get("head") != args.expected_head or payload.get("observed_head") != args.expected_head
        or payload.get("branch") != args.head or payload.get("publication_state") != "pushed"
    ):
        raise RuntimeError("Native source response does not confirm the selected candidate; reconcile before retrying")
    return 0, payload
