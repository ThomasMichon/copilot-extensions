"""Fast stdlib-only Tasks-board client for the Picker provider."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

GROUPS = ("Blocked", "Proposed", "Queued", "Started", "Completed", "Abandoned")
TERMINAL = frozenset({"Completed", "Abandoned"})
ACTIVITY_TTL_SECONDS = 90.0


def _local_machine() -> str | None:
    value = os.environ.get("AGENT_DISPATCH_SUPERVISE_MACHINE")
    root = Path(
        os.environ.get("AGENT_DISPATCH_INSTALL_DIR")
        or (Path.home() / ".agent-dispatch")
    )
    if not value:
        try:
            value = (root / "machine").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if not value:
        try:
            for line in (root / "supervisor.env").read_text(
                encoding="utf-8"
            ).splitlines():
                key, sep, candidate = line.partition("=")
                if sep and key.strip() == "AGENT_DISPATCH_SUPERVISE_MACHINE":
                    value = candidate.strip().strip("\"'")
                    break
        except OSError:
            pass
    return (value or platform.node() or "").strip().casefold() or None


def _endpoint() -> str:
    explicit = os.environ.get("AGENT_DISPATCH_URL")
    if explicit:
        return explicit.rstrip("/")
    root = Path(
        os.environ.get("AGENT_DISPATCH_ROUTING_DIR")
        or (Path.home() / ".agent-dispatch")
    )
    try:
        data = json.loads((root / "active.json").read_text(encoding="utf-8"))
        active = data.get("active") or {}
        if active.get("bind") and active.get("port"):
            bind = str(active["bind"]).strip()
            if bind in {"0.0.0.0", "*"}:
                bind = "127.0.0.1"
            elif bind in {"::", "[::]"}:
                bind = "[::1]"
            return f"http://{bind}:{int(active['port'])}"
    except (OSError, ValueError, TypeError):
        pass
    endpoint = os.environ.get("AGENT_DISPATCH_ENDPOINT")
    if not endpoint:
        run_dir = Path(
            os.environ.get("AGENT_DISPATCH_RUN_DIR") or (root / "run")
        )
        try:
            endpoint = json.loads(
                (run_dir / "endpoint.json").read_text(encoding="utf-8")
            ).get("endpoint")
        except (OSError, ValueError, TypeError, AttributeError):
            endpoint = None
    if endpoint:
        value = str(endpoint).rstrip("/")
        return value if "://" in value else f"http://{value}"
    raise RuntimeError("agent-dispatch coordinator endpoint is unavailable")


def _group(task: dict) -> str:
    status = task.get("status")
    if status == "completed":
        return "Completed"
    if status in {"abandoned", "dead_letter"}:
        return "Abandoned"
    if task.get("awaiting_steer"):
        return "Blocked"
    if status == "proposed":
        return "Proposed"
    if status == "queued":
        return "Queued"
    return "Started"


def _activity(task: dict, now: float) -> str | None:
    value = task.get("activity")
    if value not in {"ACTIVE", "STALLED"}:
        return None
    try:
        observed = float(task.get("activity_updated_at"))
    except (TypeError, ValueError):
        return None
    return value if now - observed <= ACTIVITY_TTL_SECONDS else None


def _repo_name(value: object) -> str | None:
    text = str(value or "").rstrip("/")
    return text.rsplit("/", 1)[-1].removesuffix(".git") if text else None


def _sort_timestamp(task: dict) -> float:
    try:
        return float(task.get("updated_at") or task.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _build(tasks: list[dict], *, machine: str, recent_mins: int) -> list[dict]:
    now = time.time()
    cutoff = now - max(0, recent_mins) * 60
    rows: list[dict] = []
    for task in tasks:
        target = task.get("target_machine")
        if target and str(target).casefold() != machine.casefold():
            continue
        group = _group(task)
        if group in TERMINAL:
            try:
                terminal_at = float(
                    task.get("completed_at") or task.get("updated_at") or 0
                )
            except (TypeError, ValueError):
                terminal_at = 0
            if terminal_at < cutoff:
                continue
        row = dict(task)
        row["group"] = group
        row["activity"] = _activity(task, now)
        row.setdefault("repo_name", _repo_name(task.get("repo")))
        progress = row.get("latest_progress")
        if isinstance(progress, str) and progress:
            try:
                row["latest_progress"] = json.loads(progress)
            except (ValueError, TypeError):
                pass
        rows.append(row)
    rows.sort(
        key=lambda task: (
            GROUPS.index(task["group"]),
            -_sort_timestamp(task),
        )
    )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-dispatch-board")
    parser.add_argument("--machine", required=True)
    parser.add_argument("--recent-mins", type=int, default=120)
    parser.add_argument("--label")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)

    local = _local_machine()
    if local and local != args.machine.casefold():
        command = [
            "agent-dispatch",
            "inbox",
            "--machine",
            args.machine,
            "--board",
            "--recent-mins",
            str(args.recent_mins),
            "--limit",
            str(args.limit),
        ]
        if args.label:
            command.extend(["--label", args.label])
        return subprocess.run(command, check=False).returncode

    query = {
        "status": "proposed,queued,claimed,started,completed,abandoned,dead_letter",
        "limit": str(args.limit),
    }
    if args.label:
        query["label"] = args.label
    try:
        url = f"{_endpoint()}/tasks?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url)
        token = os.environ.get("AGENT_DISPATCH_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=3) as response:
            tasks = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"agent-dispatch-board: {exc}", file=sys.stderr)
        return 1
    json.dump(
        _build(tasks, machine=args.machine, recent_mins=args.recent_mins),
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
