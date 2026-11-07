"""Outline expansion, effort-remainder derivation, session backfill and offline preview."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "driving-effort-agendas"
sys.path.insert(0, str(SKILL / "scripts"))
AGENDA = importlib.import_module("agenda")
OUTLINE = importlib.import_module("agenda_outline")
BACKFILL = importlib.import_module("agenda_backfill")


def _states(graph: dict) -> dict[str, str]:
    return {item["label"]: item["state"] for item in graph["items"]}


def test_preview_replays_statuses_and_nested_children() -> None:
    outline = {"source": {"kind": "task", "ref": "fixture"}, "items": [
        {"action": "Ship the parser", "status": "done"},
        {"action": "Wire the CLI", "status": "current", "children": [
            {"action": "Add flags", "status": "done"},
            {"action": "Add help text", "status": "current"},
            {"action": "Add examples"}]},
        {"action": "Write docs"}]}

    result = OUTLINE.preview(outline)

    states = _states(result["graph"])
    assert states["Ship the parser"] == "done"
    assert states["Add flags"] == "done"
    assert states["Add help text"] == "running"
    assert states["Add examples"] == "queued"
    assert states["Write docs"] == "queued"
    cursor = next(item for item in result["graph"]["items"] if item["id"] == result["graph"]["cursor"]["item"])
    assert cursor["label"] == "Add help text"
    text = OUTLINE.render_text(result["graph"])
    assert "▶ Add help text ◀ cursor" in text


def test_expand_keeps_colliding_items_distinct() -> None:
    # Duplicate actions and NFC-equivalent explicit refs must not collapse into one semantic item.
    outline = {"source": {"kind": "task", "ref": "fixture"}, "items": [
        {"action": "Retry", "source_ref": "caf\u00e9", "status": "done"},
        {"action": "Retry", "source_ref": "cafe\u0301", "status": "done"},
        {"id": "retry-2", "action": "Something else"},
        # A disambiguated ref must not land on a ref an earlier item already took.
        {"id": "first", "action": "One", "source_ref": "source#third"},
        {"id": "second", "action": "Two", "source_ref": "source"},
        {"id": "third", "action": "Three", "source_ref": "source"}]}

    request, _ = OUTLINE.expand(outline)

    items = request["change"]["board"]["items"]
    assert len(items) == 6
    assert len({value["source_ref"] for value in items.values()}) == 6
    assert OUTLINE.preview(outline)["graph"]["items"]


def test_preview_opens_subqueue_of_reached_parent_with_default_statuses() -> None:
    outline = {"source": {"kind": "task", "ref": "fixture"}, "items": [
        {"action": "Earlier work", "status": "done"},
        {"action": "Carried parent", "children": [{"action": "Leftover A"}, {"action": "Leftover B"}]}]}

    graph = OUTLINE.preview(outline)["graph"]

    states = _states(graph)
    assert states["Leftover A"] == "running"
    assert states["Leftover B"] == "queued"


def test_adopt_replays_progress_into_new_state_home(tmp_path: Path) -> None:
    outline = {"source": {"kind": "task", "ref": "fixture"}, "provenance": "reconstructed", "items": [
        {"action": "Old request", "status": "done"},
        {"action": "Current request", "status": "current", "children": [
            {"action": "Done step", "status": "done"}, {"action": "Next step"}]}]}
    graph_module = importlib.import_module("agenda_v2_graph")
    store = importlib.import_module("agenda_store")
    home = tmp_path / "lane"

    OUTLINE.adopt(outline, home, session="fixture-session")

    state = store.read(home)
    states = _states(graph_module.graph(state["documents"], state["records"]))
    assert states == _states(OUTLINE.preview(outline)["graph"])
    assert states["Next step"] == "running"
    with pytest.raises(AGENDA.AgendaError, match="new or empty state home"):
        OUTLINE.adopt(outline, home)


def test_from_effort_outlines_open_remainder(tmp_path: Path) -> None:
    readme = tmp_path / "efforts" / "active" / "demo" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("# Demo\n\n## Plan\n\n### Phase 1\n\n- [x] Finished step\n- [ ] Open step A\n- [ ] Open step B\n"
                      "\n### Phase 2\n\n- [ ] Lone step\n\n## Validation Plan\n\n- [ ] Validate it\n", encoding="utf-8")

    outline = OUTLINE.from_effort(readme)

    assert outline["provenance"] == "derived"
    assert outline["source"] == {"kind": "effort", "ref": "efforts/active/demo/README.md"}
    labels = json.dumps(outline["items"])
    assert "Finished step" not in labels
    states = _states(OUTLINE.preview(outline)["graph"])
    assert states["Open step A"] == "running"
    assert states["Lone step"] == "queued"
    assert states["Validate it"] == "queued"


def _session(folder: Path, messages: list[tuple[str, str, str | None]], todos: list[tuple[str, str, str, str]],
             *, ended: bool) -> Path:
    events = [{"type": "session.start", "timestamp": "2026-01-01T00:00:00Z",
               "data": {"sessionId": "fixture-session", "context": {"cwd": "/work"}}}]
    events += [{"type": "user.message", "timestamp": at, "data": {"content": content, "source": source}}
               for at, content, source in messages]
    if ended:
        events.append({"type": "session.shutdown", "timestamp": "2026-01-01T05:00:00Z", "data": {}})
    folder.mkdir()
    (folder / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    with sqlite3.connect(folder / "session.db") as db:
        db.execute("CREATE TABLE todos (id TEXT, title TEXT, status TEXT, created_at TEXT)")
        db.executemany("INSERT INTO todos VALUES (?, ?, ?, ?)", todos)
    return folder


def test_backfill_reconstructs_requests_todos_and_carried_work(tmp_path: Path) -> None:
    folder = _session(tmp_path / "s", [
        ("2026-01-01T01:00:00Z", "Please build the importer for the legacy format", None),
        ("2026-01-01T01:30:00Z", "Thanks!", None),
        ("2026-01-01T01:35:00Z", "Fix the parser", None),
        ("2026-01-01T01:40:00Z", "relayed peer message that is long enough to count", "agent-bridge"),
        ("2026-01-01T01:50:00Z", "[Context Handoff -- native] Context utilization is high; checkpoint now", None),
        ("2026-01-01T02:00:00Z", "Now add an exporter that mirrors the importer", None)],
        [("parse", "Parsing legacy rows", "done", "2026-01-01 01:05:00"),
         ("dedupe", "Deduplicating rows", "pending", "2026-01-01 01:10:00"),
         ("fix", "Fixing the parser", "done", "2026-01-01 01:36:00"),
         ("export", "Writing exporter", "in_progress", "2026-01-01 02:05:00")], ended=False)

    outline = BACKFILL.outline(folder)

    assert outline["provenance"] == "reconstructed"
    assert [item["id"] for item in outline["items"]] == ["request-1", "request-2", "request-3", "carried-open-work"]
    first, short, second, carried = outline["items"]
    assert short["action"] == "Fix the parser" and short["children"][0]["action"] == "Fixing the parser"
    assert first["status"] == "done" and [child["action"] for child in first["children"]] == ["Parsing legacy rows"]
    assert second["status"] == "current" and second["children"][0]["status"] == "current"
    assert [child["action"] for child in carried["children"]] == ["Deduplicating rows"]
    result = OUTLINE.preview(outline)
    states = _states(result["graph"])
    assert states["Writing exporter"] == "running"
    # A queued parent's subqueue opens only when it runs; its children stay defined on the board.
    assert states["Open work carried from earlier requests"] == "queued"
    actions = {value["action"] for value in result["request"]["change"]["board"]["items"].values()}
    assert "Deduplicating rows" in actions


def test_backfill_cli_reports_sessions_without_operator_requests(tmp_path: Path) -> None:
    folder = _session(tmp_path / "s", [("2026-01-01T01:00:00Z", "ok", None)], [], ended=True)

    result = subprocess.run([sys.executable, str(SKILL / "scripts" / "agenda_backfill.py"), "preview", str(folder)],
                            capture_output=True, text=True, check=False)

    assert result.returncode == 2
    assert "no substantive operator requests" in result.stderr


def test_expand_rejects_unknown_item_fields() -> None:
    with pytest.raises(AGENDA.AgendaError):
        OUTLINE.expand({"source": {"kind": "task", "ref": "x"}, "items": [{"action": "a", "owner": "me"}]})
