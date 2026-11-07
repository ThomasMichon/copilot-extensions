#!/usr/bin/env python3
"""Backfill an agenda outline from a Copilot session's recorded history.

Reads a session-state folder (``events.jsonl`` plus the optional ``session.db``
todo table and checkpoint index) and produces either a compact digest an agent
can use to author a faithful outline, or a deterministic first-pass outline:
one item per operator request, with the todos created while serving that
request as its subqueue. The result is marked ``reconstructed``; it is a
starting point the agent refines, never an authoritative record of intent.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from agenda import AgendaError  # noqa: E402
from agenda_outline import SCHEMA, clip, preview, render_text  # noqa: E402

ACK_LIMIT = 60
# A short turn is an acknowledgement only when every word is conversational filler, never by length alone.
ACK_WORDS = frozenset("""
ok okay k kk yes yep yeah yup sure sounds good great cool nice perfect awesome excellent fine alright right
thanks thank you ty thx lgtm sgtm got it go ahead do that please continue proceed keep going carry on on
and that's thats works wonderful amazing love sweet
""".split())
ACTION_LIMIT = 400
# Harness-injected turns arrive without a source; they are continuity plumbing, not operator intent.
INJECTED_PREFIXES = ("[Context Handoff", "Continue the task in a fresh context window", "<system_", "<session_memory")
TODO_STATUS = {"done": "done", "in_progress": "current", "blocked": "waiting", "pending": "queued"}


def _instant(value: str) -> datetime:
    value = value.strip().replace(" ", "T")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _requests(events: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Operator-typed requests in order, plus session start/end facts."""
    requests: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"ended": False}
    with events.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind, data = event.get("type"), event.get("data") or {}
            if kind == "session.start":
                facts["started_at"] = event.get("timestamp")
                facts["cwd"] = (data.get("context") or {}).get("cwd")
                facts["session_id"] = data.get("sessionId")
            elif kind == "session.shutdown":
                facts["ended"] = True
            elif kind == "session.resume":
                facts["ended"] = False
            elif kind == "user.message" and data.get("source") is None:
                content = (data.get("content") or "").strip()
                if content and not content.startswith(INJECTED_PREFIXES):
                    requests.append({"at": event.get("timestamp"), "content": content})
    return requests, facts


def acknowledgement(content: str) -> bool:
    words = re.findall(r"[a-z']+", content.lower())
    return len(content) <= ACK_LIMIT and all(word in ACK_WORDS for word in words)


def _todos(database: Path) -> list[dict[str, Any]]:
    if not database.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            rows = db.execute("SELECT id, title, status, created_at FROM todos ORDER BY created_at, rowid").fetchall()
    except sqlite3.Error:
        return []
    return [{"id": row[0], "title": row[1], "status": row[2] or "pending", "created_at": row[3]}
            for row in rows if row[1]]


def _checkpoints(folder: Path) -> list[str]:
    index = folder / "checkpoints" / "index.md"
    if not index.is_file():
        return []
    titles = []
    for line in index.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0].isdigit():
            titles.append(cells[1])
    return titles


def digest(folder: Path) -> dict[str, Any]:
    """Compact, bounded evidence for authoring a reconstructed outline."""
    folder = folder.resolve()
    events = folder / "events.jsonl"
    if not events.is_file():
        raise AgendaError(f"no events.jsonl in {folder}")
    requests, facts = _requests(events)
    todos = _todos(folder / "session.db")
    for request in requests:
        request["acknowledgement"] = acknowledgement(request["content"])
        request["todos"] = []
    substantive = [request for request in requests if not request["acknowledgement"]]
    for todo in todos:
        try:
            created = _instant(todo["created_at"])
        except (ValueError, AttributeError):
            continue
        owner = None
        for request in substantive:
            if _instant(request["at"]) <= created:
                owner = request
        if owner is not None:
            owner["todos"].append(todo)
    return {"session": facts.get("session_id") or folder.name, "cwd": facts.get("cwd"),
            "started_at": facts.get("started_at"), "ended": facts["ended"],
            "requests": requests, "checkpoints": _checkpoints(folder),
            "unassigned_todos": [todo for todo in todos
                                 if not any(todo in request["todos"] for request in substantive)]}


def _todo_item(todo: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"todo-{todo['id']}", "action": clip(todo["title"], ACTION_LIMIT),
            "status": TODO_STATUS.get(todo["status"], "queued"), "evidence": f"session todo {todo['id']}"}


def outline(folder: Path) -> dict[str, Any]:
    """Deterministic first-pass outline: requests in order, todos as subqueues, open work last."""
    facts = digest(folder)
    substantive = [request for request in facts["requests"] if not request["acknowledgement"]]
    if not substantive:
        raise AgendaError("session has no substantive operator requests to reconstruct")
    items: list[dict[str, Any]] = []
    carried: list[dict[str, Any]] = []
    for index, request in enumerate(substantive):
        last = index == len(substantive) - 1
        children = [_todo_item(todo) for todo in request["todos"]]
        finished = [child for child in children if child["status"] == "done"]
        unfinished = [child for child in children if child["status"] != "done"]
        item: dict[str, Any] = {"id": f"request-{index + 1}", "label": clip(request["content"].splitlines()[0]),
                                "action": clip(request["content"], ACTION_LIMIT),
                                "evidence": f"operator request at {request['at']}"}
        if last and (unfinished or not facts["ended"]):
            item["status"] = "current"
            if children:
                item["children"] = finished + unfinished
                if unfinished and unfinished[0]["status"] == "queued":
                    unfinished[0]["status"] = "current"
        else:
            # Later requests superseded this one; any open todos are carried to the end, not lost.
            item["status"] = "done"
            if finished:
                item["children"] = finished
            carried.extend(unfinished)
        items.append(item)
    if carried:
        for child in carried:
            child["status"] = "queued"
        items.append({"id": "carried-open-work", "label": "Open work carried from earlier requests",
                      "action": "Resolve or explicitly drop todos left open by earlier requests.",
                      "status": "queued", "children": carried})
    started = (facts.get("started_at") or "")[:10]
    return {"schema": SCHEMA, "version": 1, "provenance": "reconstructed",
            "source": {"kind": "task", "ref": f"session {facts['session']}" + (f" ({started})" if started else "")},
            "worktree": f"session-{facts['session']}", "items": items}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("digest", "outline", "preview"))
    parser.add_argument("session", type=Path, help="session-state folder (contains events.jsonl)")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="preview format")
    args = parser.parse_args(argv)
    try:
        if args.command == "digest":
            output: Any = digest(args.session)
        elif args.command == "outline":
            output = outline(args.session)
        else:
            built = outline(args.session)
            result = preview(built)
            if args.format == "text":
                print(render_text(result["graph"]))
                for warning in result["warnings"]:
                    print(f"warning: {warning}", file=sys.stderr)
                return 0
            output = {"outline": built, **result}
    except (AgendaError, OSError) as exc:
        print(f"agenda-backfill: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
