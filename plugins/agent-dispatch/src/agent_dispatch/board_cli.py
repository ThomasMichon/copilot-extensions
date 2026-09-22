"""Fast stdlib-only Tasks-board client for the Picker provider."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .install_paths import install_dir
from .procutil import no_window_kwargs as _no_window_kwargs
from .procutil import windowless_python, windowless_python_env
from .worktree_status_relay import board_fields_for_task, claimed_identity

#: Operator feedback 2026-09-20: Started is more interesting to inspect at a
#: glance than Queued (a task not yet running), so it sits right after
#: Blocked/Proposed. `__main__.py`'s `_BOARD_GROUPS` is a byte-identical
#: duplicate (used by the delegated `inbox` CLI path) and must stay in sync.
GROUPS = (
    "Blocked",
    "Proposed",
    "Started",
    "Queued",
    "Suspended",
    "Completed",
    "Abandoned",
)
TERMINAL = frozenset({"Completed", "Abandoned"})
ACTIVITY_TTL_SECONDS = 90.0
_RELAY_ENDPOINT: str | None = None


def _local_machine() -> str | None:
    value = os.environ.get("AGENT_DISPATCH_SUPERVISE_MACHINE")
    root = install_dir()
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
    root = Path(os.environ.get("AGENT_DISPATCH_ROUTING_DIR") or install_dir())
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
    if status == "suspended":
        return "Suspended"
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


def _wt_live(activity: str | None, task: dict, now: float) -> str | None:
    """The Tasks pane's ``LIVE`` column (Phase 3): a compact, at-a-glance
    liveness string reusing ``activity``/``activity_updated_at`` -- fields
    already computed above, self-reported by a **headless** worker via
    ``agent-dispatch activity`` -- rather than a fresh subprocess/bridge probe
    (this board client is stdlib-only and re-runs on every Picker refresh, so
    a per-row liveness probe was ruled out; see the effort's Runbook).

    Returns ``"active"`` / ``"stalled Nm"`` (elapsed minutes since the last
    beat) for a headless body with a fresh signal, else ``None`` (blank) --
    including for a CLI-embodied task (item 3), which never calls
    ``set_activity`` and so has no cheap liveness signal available here. A
    blank cell is therefore "no headless liveness signal", not a confirmed
    "not live" -- a real interactive session may still be running.
    """
    if activity == "ACTIVE":
        return "active"
    if activity == "STALLED":
        try:
            observed = float(task.get("activity_updated_at"))
        except (TypeError, ValueError):
            return "stalled"
        minutes = max(0, int((now - observed) // 60))
        return f"stalled {minutes}m"
    return None


def _repo_name(value: object) -> str | None:
    text = str(value or "").rstrip("/")
    return text.rsplit("/", 1)[-1].removesuffix(".git") if text else None


def _sort_timestamp(task: dict) -> float:
    try:
        return float(task.get("updated_at") or task.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def _relay_fetch(repo: str, worktree_id: str) -> dict | None:
    endpoint = _RELAY_ENDPOINT or _endpoint()
    request = urllib.request.Request(
        f"{endpoint}/worktree-status-relay?"
        f"{urllib.parse.urlencode({'repo': repo, 'worktree_id': worktree_id})}"
    )
    token = os.environ.get("AGENT_DISPATCH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return payload if isinstance(payload, dict) else None


def _relay_fetch_many(refs: list[tuple[str, str]]) -> dict[tuple[str, str], dict | None]:
    if not refs:
        return {}
    endpoint = _RELAY_ENDPOINT or _endpoint()
    payload = json.dumps(
        [{"repo": repo, "worktree_id": worktree_id} for repo, worktree_id in refs]
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint}/worktree-status-relays",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = os.environ.get("AGENT_DISPATCH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return {
        (row["repo"], row["worktree_id"]): row.get("entry")
        for row in rows
        if isinstance(row, dict)
    }


def _build(
    tasks: list[dict],
    *,
    machine: str,
    recent_mins: int,
    relay_fetch=None,
    relay_fetch_many=None,
) -> list[dict]:
    now = time.time()
    cutoff = now - max(0, recent_mins) * 60
    rows: list[dict] = []
    relay_cache: dict[tuple[str, str], dict | None] = {}
    relay_keys: list[tuple[str, str]] = []

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
        row["wt_live"] = _wt_live(row["activity"], task, now)
        _claimed_machine, claimed_worktree = claimed_identity(task)
        if claimed_worktree and not row.get("target_worktree"):
            row["target_worktree"] = claimed_worktree
        # PR #2913 review: the manifest's mutating/card actions gate on these
        # booleans -- an unpopulated field degrades to falsy in the picker's
        # `when` matcher, so leaving one out doesn't crash anything, but it
        # DOES silently hide (or, worse, wrongly show) the action it gates.
        # Populate every field the manifest actually depends on today from
        # data already on the task; only `cli_openable` and `has_charter`
        # stay hard-`False` pending their own follow-on phases (see the two
        # comments below for exactly why).
        row["has_worktree"] = bool(claimed_worktree)
        # `embodied` gates Pause/Force-stop: true only for a task with a
        # genuinely LIVE session (`started`). A "Blocked" task's real status
        # is `suspended` (see `set_card`'s own docstring: posting a card with
        # a form atomically suspends the task so its worker process CAN be
        # stopped) -- so `embodied` must NOT include Blocked, or force-stop
        # would be offered on a task with no live session to stop.
        row["embodied"] = task.get("status") == "started"
        row["held"] = bool(task.get("hold_reason"))
        # `cli_openable` is deliberately hard-`False` for now: the manifest's
        # `open-cli` action is `kind: internal, verb: open-cli`, which the
        # Picker dispatches to `_open_worktree_cli` -- the GENERIC Worktrees
        # open-into-CLI handler. That handler assumes an already-existing
        # worktree row and has no idea about Phase 1 item 3's ownership
        # transaction (`launch_interactive_embodiment` / `agent-dispatch
        # embody --interactive`): a Proposed/Queued task has no
        # `target_worktree` yet (nothing for the generic handler to find),
        # and a Suspended task's re-embodiment needs the fenced
        # claim/adopt-owner-session dance the generic handler bypasses
        # entirely. Wiring a dedicated internal verb that calls
        # `agent-dispatch embody --interactive` is Phase 7's own scope
        # ("pure UI wiring, no new backend logic") -- until that lands,
        # keeping this `False` keeps the action schema-visible (a reviewer
        # or operator can see it's designed-for) but never actually
        # reachable, rather than reachable-and-wrong.
        row["cli_openable"] = False
        # `has_charter` similarly stays `False`: nothing today populates a
        # real `charter.*` object (title/status/link/body), so leaving the
        # action ungated would render an empty card. Gating it behind this
        # field (mirroring `worktree-status`'s own `has_worktree` gate)
        # keeps it schema-visible without showing a broken empty card.
        row["has_charter"] = False
        row.setdefault("repo_name", _repo_name(task.get("repo")))
        repo = str(row.get("repo") or "")
        if repo and claimed_worktree:
            relay_keys.append((repo, claimed_worktree))
        progress = row.get("latest_progress")
        if isinstance(progress, str) and progress:
            try:
                row["latest_progress"] = json.loads(progress)
            except (ValueError, TypeError):
                pass
        rows.append(row)
    try:
        if relay_keys:
            fetch_many = relay_fetch_many or _relay_fetch_many
            relay_cache = fetch_many(list(dict.fromkeys(relay_keys)))
    except Exception:
        relay_cache = {}
    if not relay_cache and relay_fetch is not None:
        for repo, worktree_id in dict.fromkeys(relay_keys):
            try:
                relay_cache[(repo, worktree_id)] = relay_fetch(repo, worktree_id)
            except Exception:
                relay_cache[(repo, worktree_id)] = None
    for row in rows:
        _claimed_machine, claimed_worktree = claimed_identity(row)
        repo = str(row.get("repo") or "")
        row.update(
            board_fields_for_task(
                row,
                relay_cache.get((repo, claimed_worktree))
                if repo and claimed_worktree
                else None,
                now=now,
            )
        )
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
        python = sys.executable
        command = [
            windowless_python(python),
            "-m",
            "agent_dispatch",
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
        env = dict(os.environ)
        env.update(windowless_python_env(python))
        return subprocess.run(
            command, check=False, env=env, **_no_window_kwargs()
        ).returncode

    query = {
        "status": (
            "proposed,queued,claimed,started,suspended,"
            "completed,abandoned,dead_letter"
        ),
        "limit": str(args.limit),
    }
    if args.label:
        query["label"] = args.label
    try:
        endpoint = _endpoint()
        url = f"{endpoint}/tasks?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url)
        token = os.environ.get("AGENT_DISPATCH_TOKEN")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=3) as response:
            tasks = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"agent-dispatch-board: {exc}", file=sys.stderr)
        return 1
    global _RELAY_ENDPOINT
    _RELAY_ENDPOINT = endpoint
    json.dump(
        _build(
            tasks,
            machine=args.machine,
            recent_mins=args.recent_mins,
            relay_fetch=_relay_fetch,
            relay_fetch_many=_relay_fetch_many,
        ),
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
