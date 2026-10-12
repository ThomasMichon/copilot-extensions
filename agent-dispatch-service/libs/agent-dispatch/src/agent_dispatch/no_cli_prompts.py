"""Autopilot seed prompts for workers without a local ``agent-dispatch`` CLI.

Phase 3 of the ``agent-dispatch-worker-operating-procedures`` effort is the
reachability-tiered complement to :mod:`agent_dispatch.embody_prompts`:
workers that cannot run ``agent-dispatch charter show`` (or any other local
CLI verb) still need the same behavioral contract and a concrete, structured
status/lifecycle channel. This module keeps that no-CLI shape pure and
side-effect free so it can be unit-tested as text, just like the CLI-backed
seed builders.
"""

from __future__ import annotations

import textwrap
from typing import Literal

from .worker_charter import (
    AUTOPILOT_CHARTER_NAME,
    OPERATING_PROCEDURES_TEXT,
    charter_text,
)

NoCliEnvironment = Literal["codespace", "container", "machine", "unaffiliated"]

_DISPLAY_NAMES: dict[NoCliEnvironment, str] = {
    "codespace": "a CodeSpace",
    "container": "a container",
    "machine": "a different machine",
    "unaffiliated": "a cross-repo or otherwise unaffiliated agent",
}

_REACHBACK_NOTES: dict[NoCliEnvironment, str] = {
    "codespace": (
        "This CodeSpace tier already has a real reach-back path in the current "
        "stack: `agent_codespaces._peer_launch.peer_environment()` explicitly "
        "forwards `AGENT_DISPATCH_URL` / `AGENT_DISPATCH_TOKEN` plus the shared "
        "coordinator variants into the remote venue. Use those exact values if "
        "they are present; do not invent or rewrite the endpoint."
    ),
    "container": (
        "This container tier already has a real reach-back path in the current "
        "stack: `agent_containers._peer_launch.peer_environment()` explicitly "
        "forwards `AGENT_DISPATCH_URL` / `AGENT_DISPATCH_TOKEN` plus the shared "
        "coordinator variants into the remote venue. Use those exact values if "
        "they are present; do not invent or rewrite the endpoint."
    ),
    "machine": (
        "For a genuinely different machine, the shipped reach-back paths today "
        "are the pre-staged `AGENT_DISPATCH_URL` / `AGENT_DISPATCH_TOKEN` pair "
        "or the shared-coordinator fallback (`AGENT_DISPATCH_SHARED_URL` plus "
        "`AGENT_DISPATCH_SHARED_TOKEN` / `AGENT_DISPATCH_SHARED_TOKEN_COMMAND`). "
        "Use whichever one this environment already exposes; do not assume SSH "
        "back to the origin unless the seed explicitly says so."
    ),
    "unaffiliated": (
        "This unaffiliated tier may know nothing about agent-dispatch ahead of "
        "time. Treat the coordinator URL/token the launcher provided (or the "
        "shared-coordinator environment, if that is what you were given) as the "
        "entire control-plane contract. If neither is present, you do not have a "
        "dispatch channel and must stop."
    ),
}

_HTTP_HELPER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import argparse
    import json
    import os
    import subprocess
    import urllib.error
    import urllib.request


    def fail(message):
        raise SystemExit(message)


    def base_url():
        return (
            os.environ.get("AGENT_DISPATCH_URL")
            or os.environ.get("AGENT_DISPATCH_SHARED_URL")
            or fail(
                "missing AGENT_DISPATCH_URL/AGENT_DISPATCH_SHARED_URL; "
                "the dispatch control plane was not provisioned in this venue"
            )
        )


    def token():
        direct = (
            os.environ.get("AGENT_DISPATCH_TOKEN")
            or os.environ.get("AGENT_DISPATCH_SHARED_TOKEN")
        )
        if direct:
            return direct
        command = os.environ.get("AGENT_DISPATCH_SHARED_TOKEN_COMMAND")
        if not command:
            return None
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        value = completed.stdout.strip()
        if completed.returncode != 0 or not value:
            fail(
                "AGENT_DISPATCH_SHARED_TOKEN_COMMAND did not yield a token; "
                "stop and report the control plane unavailable"
            )
        return value


    def call(method, path, body=None):
        headers = {"Content-Type": "application/json"}
        bearer = token()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            base_url().rstrip("/") + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                print(json.dumps(json.load(response), indent=2, sort_keys=True))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            fail(f"HTTP {exc.code}: {detail}")


    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show")
    show.add_argument("task_id")

    claim = sub.add_parser("claim-eval")
    claim.add_argument("task_id")
    claim.add_argument("--worker", required=True)
    claim.add_argument("--repo")
    claim.add_argument("--all-repos", action="store_true")
    claim.add_argument("--capability", action="append", default=[])

    start = sub.add_parser("start")
    start.add_argument("task_id")
    start.add_argument("--worker", required=True)

    steer_take = sub.add_parser("steer-take")
    steer_take.add_argument("task_id")
    steer_take.add_argument("--worker", required=True)
    steer_take.add_argument("--all", dest="all_pending", action="store_true")

    progress = sub.add_parser("progress")
    progress.add_argument("task_id")
    progress.add_argument("--worker", required=True)
    progress.add_argument("--summary", required=True)
    progress.add_argument("--phase", default="")
    progress.add_argument("--blocker")
    progress.add_argument("--pr")

    suspend = sub.add_parser("suspend")
    suspend.add_argument("task_id")
    suspend.add_argument("--worker", required=True)
    suspend.add_argument("--reason", required=True)

    complete = sub.add_parser("complete")
    complete.add_argument("task_id")
    complete.add_argument("--worker", required=True)
    complete.add_argument("--result-ref", required=True)

    decline = sub.add_parser("yield")
    decline.add_argument("task_id")
    decline.add_argument("--worker", required=True)
    decline.add_argument("--note", required=True)

    abandon = sub.add_parser("abandon")
    abandon.add_argument("task_id")
    abandon.add_argument("--worker", required=True)
    abandon.add_argument("--reason")
    abandon.add_argument("--duplicate-of")

    args = parser.parse_args()

    if args.command == "show":
        call("GET", f"/tasks/{args.task_id}")
    elif args.command == "claim-eval":
        call(
            "POST",
            "/claim",
            {
                "task_id": args.task_id,
                "worker_id": args.worker,
                "evaluation": True,
                "repo": args.repo,
                "all_repos": args.all_repos,
                "capabilities": args.capability,
            },
        )
    elif args.command == "start":
        call("POST", f"/tasks/{args.task_id}/start", {"worker_id": args.worker})
    elif args.command == "steer-take":
        call(
            "POST",
            f"/tasks/{args.task_id}/steer/take",
            {"worker_id": args.worker, "all_pending": args.all_pending},
        )
    elif args.command == "progress":
        call(
            "POST",
            f"/tasks/{args.task_id}/progress",
            {
                "worker_id": args.worker,
                "phase": args.phase,
                "summary": args.summary,
                "blocker": args.blocker,
                "pr": args.pr,
            },
        )
    elif args.command == "suspend":
        call(
            "POST",
            f"/tasks/{args.task_id}/suspend",
            {"worker_id": args.worker, "reason": args.reason},
        )
    elif args.command == "complete":
        call(
            "POST",
            f"/tasks/{args.task_id}/complete",
            {"worker_id": args.worker, "result_ref": args.result_ref},
        )
    elif args.command == "yield":
        call(
            "POST",
            f"/tasks/{args.task_id}/yield",
            {"worker_id": args.worker, "note": args.note},
        )
    elif args.command == "abandon":
        reason = args.reason
        if args.duplicate_of:
            dedup_note = f"duplicate of {args.duplicate_of}"
            reason = f"{reason}; {dedup_note}" if reason else dedup_note
        call(
            "POST",
            f"/tasks/{args.task_id}/abandon",
            {
                "worker_id": args.worker,
                "permitted": True,
                "reason": reason,
            },
        )
    """
).rstrip()


def _lane_flag_text(*, repo: str | None, all_repos: bool) -> str:
    if repo and all_repos:
        raise ValueError("no-CLI claim scope cannot set repo and all_repos")
    if all_repos:
        return " --all-repos"
    if repo:
        return f" --repo {repo}"
    return ""


def _environment_note(environment: NoCliEnvironment) -> str:
    try:
        return _REACHBACK_NOTES[environment]
    except KeyError:
        raise ValueError(
            f"unknown no-CLI environment {environment!r}; expected one of "
            f"{sorted(_REACHBACK_NOTES)}"
        ) from None


def _environment_display_name(environment: NoCliEnvironment) -> str:
    try:
        return _DISPLAY_NAMES[environment]
    except KeyError:
        raise ValueError(
            f"unknown no-CLI environment {environment!r}; expected one of "
            f"{sorted(_REACHBACK_NOTES)}"
        ) from None


def no_cli_autopilot_worker_prompt(
    task_id: str,
    *,
    worker_id: str,
    environment: NoCliEnvironment,
    repo: str | None = None,
    all_repos: bool = False,
) -> str:
    """Build the full-inline autopilot seed for a worker with no local CLI.

    This is intentionally the complement to the Phase 2 CLI-capable seeds:
    because the worker cannot run ``agent-dispatch charter show`` (or any other
    local verb), both the universal operating procedures and the task-type
    autopilot charter are inlined directly, alongside a concrete HTTP helper
    that exercises the coordinator routes without the CLI.
    """
    lane = _lane_flag_text(repo=repo, all_repos=all_repos)
    display_name = _environment_display_name(environment)
    env_note = _environment_note(environment)
    autopilot_text = charter_text(AUTOPILOT_CHARTER_NAME)
    return (
        f"You are a dispatched agent-dispatch **autopilot** worker (worker id: "
        f"{worker_id}) running in {display_name} without a local "
        f"`agent-dispatch` CLI. You cannot fetch charters by name or rely on "
        f"worktree identity discovery here, so the full contracts and the "
        f"structured status channel are inlined directly. {env_note} If the "
        f"needed coordinator URL/token are missing or any call fails, follow the "
        f"operating procedures below exactly: stop, do not self-repair the "
        f"control plane, and say so plainly.\n\n"
        "Universal operating procedures (inline because this tier cannot fetch "
        "them by name):\n\n"
        f"{OPERATING_PROCEDURES_TEXT}\n\n"
        "Task-type autopilot charter (also inline because this tier cannot fetch "
        "it by name either):\n\n"
        f"{autopilot_text}\n\n"
        "Status/lifecycle helper: write this exact file to `dispatch_http.py` in "
        "a writable scratch location, then use it for every structured "
        "agent-dispatch call from this environment. If `python` is unavailable "
        "but `python3` exists, substitute `python3` in every command below "
        "(common in container / CodeSpace Linux images):\n\n"
        f"```python\n{_HTTP_HELPER}\n```\n\n"
        f"Lifecycle commands for task {task_id} in this tier:\n"
        f"1. Read the task: `python dispatch_http.py show {task_id}`.\n"
        f"2. Claim it for evaluation: `python dispatch_http.py claim-eval "
        f"{task_id} --worker {worker_id}{lane}` (repeat `--capability <cap>` for "
        f"every required capability).\n"
        f"3. While you hold the evaluation lease, follow the inline autopilot "
        f"charter's duplicate / feasibility / fit checks.\n"
        f"4. On ACCEPT per that charter, `python dispatch_http.py start {task_id} "
        f"--worker {worker_id}`, then `python dispatch_http.py steer-take "
        f"{task_id} --worker {worker_id} --all`.\n"
        f"5. Record every real status beat with `python dispatch_http.py progress "
        f"{task_id} --worker {worker_id} --phase <phase> --summary "
        f'"<one line>"` (add `--pr <ref>` or `--blocker "<why>"` when relevant).\n'
        f"6. If the task is not for you or you hit a transient blocker, return it "
        f"to the queue with `python dispatch_http.py yield {task_id} --worker "
        f"{worker_id} --note \"<why>\"`.\n"
        f"7. If you must preserve the work for a later resume instead of "
        f"re-queueing it, suspend it with `python dispatch_http.py suspend "
        f"{task_id} --worker {worker_id} --reason \"<why>\"`.\n"
        f"8. If it is a duplicate or obsolete, retire it terminally with "
        f"`python dispatch_http.py abandon {task_id} --worker {worker_id} "
        f"--duplicate-of <ref>` (or `--reason \"<why>\"`) so the dedup is "
        f"recorded, never a silent drop.\n"
        f"9. Only once the charters say the goal is genuinely met, complete it "
        f"with `python dispatch_http.py complete {task_id} --worker {worker_id} "
        f"--result-ref <ref>`."
    )
