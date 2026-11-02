"""Focused tests for the optional read-only agenda inspector."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
import sys
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "driving-effort-agendas"
SPEC = importlib.util.spec_from_file_location(
    "efforts_agenda_inspector", SKILL / "scripts" / "agenda.py"
)
assert SPEC and SPEC.loader
AGENDA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AGENDA
SPEC.loader.exec_module(AGENDA)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    board = json.loads((SKILL / "assets" / "agenda.example.json").read_text())
    board["revision"] = 1
    cursor = json.loads((SKILL / "assets" / "cursor.example.json").read_text())
    cursor["worktree"] = "fixture-worktree"
    cursor["frames"][0]["agenda_revision"] = 1
    (tmp_path / "agenda.json").write_text(json.dumps(board), encoding="utf-8")
    (tmp_path / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")
    return tmp_path


def edit(home: Path, name: str, mutate: Callable[[dict[str, Any]], object]) -> None:
    path = home / name
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_next_is_read_only_and_preserves_unknown_metadata(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edit(home, "cursor.json", lambda c: c.update({"future": {"retained": True}}))
    before = {p.name: p.read_bytes() for p in home.iterdir()}
    assert AGENDA.main(["next", "--state-home", str(home), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["worktree"] == "fixture-worktree"
    assert "regression" in result["next_action"]
    assert result["declared_outcomes"] == {"ready": "validate"}
    assert before == {p.name: p.read_bytes() for p in home.iterdir()}


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--expected-revision", "7"], "revision changed"),
        (["--worktree", "another-worktree"], "different worktree"),
    ],
)
def test_wrong_snapshot_refused_without_writes(
    home: Path, argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (home / "cursor.json").read_bytes()
    assert AGENDA.main(["check", "--state-home", str(home), *argv]) == 2
    assert message in capsys.readouterr().err
    assert (home / "cursor.json").read_bytes() == before


def test_nested_return_must_name_immediate_parent(home: Path) -> None:
    child = json.loads((home / "agenda.json").read_text())
    child["source"] = {"kind": "task", "ref": "prerequisite"}
    (home / "child.json").write_text(json.dumps(child), encoding="utf-8")
    cursor = json.loads((home / "cursor.json").read_text())
    frame = dict(cursor["frames"][0])
    frame.update(
        agenda="child.json",
        return_to={
            "agenda": "agenda.json",
            "node": "validate",
            "next_action": "Revalidate parent",
        },
    )
    cursor["frames"].append(frame)
    (home / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")
    assert len(AGENDA.inspect(home).stack) == 2
    edit(
        home,
        "cursor.json",
        lambda c: c["frames"][-1]["return_to"].update(agenda="child.json"),
    )
    with pytest.raises(AGENDA.AgendaError, match="parent"):
        AGENDA.inspect(home)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda c: c["frames"][0].update(node="gone"), "node does not exist"),
        (lambda c: c["frames"][0]["visits"].update(implement=4), "visit budget"),
        (lambda c: c.update(detours_without_progress=3), "detour budget"),
        (lambda c: c.update(frames=c["frames"] * 4), "depth budget"),
        (lambda c: c.update(status="complete"), "nonterminal work"),
        (lambda c: c.update(status=[]), "unsupported cursor status"),
    ],
)
def test_invalid_cursor_cannot_look_ready(
    home: Path, mutate: Callable[[dict[str, Any]], object], message: str
) -> None:
    edit(home, "cursor.json", mutate)
    with pytest.raises(AGENDA.AgendaError, match=message):
        AGENDA.inspect(home)


def test_changed_definition_and_bad_edge_refused(home: Path) -> None:
    edit(home, "agenda.json", lambda a: a.update(revision=2))
    with pytest.raises(AGENDA.AgendaError, match="agenda revision"):
        AGENDA.inspect(home)
    edit(
        home,
        "agenda.json",
        lambda a: a["nodes"]["implement"]["on"].update(ready="missing"),
    )
    with pytest.raises(AGENDA.AgendaError, match="destination"):
        AGENDA.inspect(home)


def test_invalid_source_shape_is_a_diagnostic_not_a_traceback(home: Path) -> None:
    edit(home, "agenda.json", lambda a: a["source"].update(kind=[]))
    with pytest.raises(AGENDA.AgendaError, match="source kind"):
        AGENDA.inspect(home)


def test_legacy_manual_completed_cursor_and_queued_root(home: Path) -> None:
    edit(
        home,
        "cursor.json",
        lambda c: (
            c.pop("version"),
            c.update(
                status="complete",
                frames=[],
                completed_roots=[{"agenda": "agenda.json"}],
            ),
        ),
    )
    result = AGENDA.inspect(home)
    assert result.status == "complete" and any(
        "Legacy" in note for note in result.notes
    )
    edit(
        home, "cursor.json", lambda c: c.update(status="working", queue=["agenda.json"])
    )
    assert "already-assigned" in AGENDA.inspect(home).next_action


@pytest.mark.parametrize(
    "raw",
    [
        '{"version": 1, "version": 2}',
        '{"revision": NaN}',
        "not json",
    ],
)
def test_corrupt_json_is_not_an_empty_agenda(home: Path, raw: str) -> None:
    (home / "cursor.json").write_text(raw, encoding="utf-8")
    with pytest.raises(AGENDA.AgendaError):
        AGENDA.inspect(home)


def test_board_cannot_escape_state_home(home: Path) -> None:
    edit(
        home, "cursor.json", lambda c: c["frames"][0].update(agenda="../unrelated.json")
    )
    with pytest.raises(AGENDA.AgendaError, match="escapes"):
        AGENDA.inspect(home)


def test_steering_does_not_skip_to_a_queued_assignment(home: Path) -> None:
    edit(
        home,
        "cursor.json",
        lambda c: c.update(
            status="steering",
            frames=[],
            queue=["agenda.json"],
            next_action="Obtain the missing scope decision",
        ),
    )
    assert AGENDA.inspect(home).next_action == "Obtain the missing scope decision"


def test_oversized_snapshot_is_refused_without_rewriting(home: Path) -> None:
    path = home / "cursor.json"
    original = json.dumps({"padding": "x" * 131072}).encode()
    path.write_bytes(original)
    with pytest.raises(AGENDA.AgendaError, match="128 KiB"):
        AGENDA.inspect(home)
    assert path.read_bytes() == original


@pytest.mark.parametrize("location", ["active", "queued"])
def test_nul_agenda_path_is_a_no_write_diagnostic(
    home: Path, location: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if location == "active":
        edit(
            home, "cursor.json", lambda c: c["frames"][0].update(agenda="bad\x00.json")
        )
    else:
        edit(home, "cursor.json", lambda c: c.update(queue=["bad\x00.json"]))
    before = (home / "cursor.json").read_bytes()
    assert AGENDA.main(["check", "--state-home", str(home)]) == 2
    assert "NUL" in capsys.readouterr().err
    assert (home / "cursor.json").read_bytes() == before


def test_invalid_state_home_is_a_diagnostic(capsys: pytest.CaptureFixture[str]) -> None:
    assert AGENDA.main(["next", "--state-home", "bad\x00directory"]) == 2
    assert "state home" in capsys.readouterr().err


@pytest.fixture
def deferred_home(home: Path) -> Path:
    cursor = json.loads((home / "cursor.json").read_text())
    branch = {
        "id": "scope-decision",
        "saved_revision": 0,
        "frames": deepcopy(cursor["frames"]),
        "max_depth": 3,
        "max_detours_without_progress": 2,
        "detours_without_progress": 1,
        "reason": "The effect remains outside current approval.",
        "decision_ref": "canonical task: pending decision",
        "decision_packet_ref": "canonical task: prepared framework",
        "attempted_at": "2026-10-31T14:48:00-07:00",
        "revisit_at": "2026-10-31T18:48:00-07:00",
        "recheck_action": "Read current decision and limits; do not replay the effect.",
    }
    other = json.loads((home / "agenda.json").read_text())
    other["source"] = {"kind": "task", "ref": "accepted-read-only-analysis"}
    (home / "other.json").write_text(json.dumps(other), encoding="utf-8")
    cursor.update(version=2, revision=1, frames=[], queue=["other.json"],
                  max_deferred=4, deferred=[branch])
    (home / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")
    return home


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.fromisoformat(f"2026-10-31T{hour:02}:{minute:02}:00-07:00")


def test_future_branch_preserved_while_another_assignment_is_usable(deferred_home: Path) -> None:
    before = {p.name: p.read_bytes() for p in deferred_home.iterdir()}
    result = AGENDA.inspect(deferred_home, now=at(15))
    assert result.cursor_version == 2
    assert "other.json" in result.next_action
    assert result.due_rechecks == []
    assert result.next_revisit_at == "2026-10-31T18:48:00-07:00"
    assert result.deferred[0].detours_without_progress == 1
    assert "not approval" in " ".join(result.notes)
    assert before == {p.name: p.read_bytes() for p in deferred_home.iterdir()}


def test_due_time_recommends_only_reinspection_not_saved_effect(deferred_home: Path) -> None:
    edit(deferred_home, "cursor.json",
         lambda c: c["deferred"][0]["frames"][0].update(next_action="Apply the gated data change"))
    result = AGENDA.inspect(deferred_home, now=at(18, 48))
    assert result.due_rechecks == ["scope-decision"]
    assert result.deferred[0].trigger == "time"
    assert result.deferred[0].resume_action == "Apply the gated data change"
    assert result.next_action.startswith("Recheck deferred branch")
    assert "Apply the gated" not in result.next_action


def test_new_response_rechecks_early_but_processed_response_does_not_poll(deferred_home: Path) -> None:
    edit(deferred_home, "cursor.json",
         lambda c: c["deferred"][0].update(response_ref="actual-response"))
    result = AGENDA.inspect(deferred_home, now=at(15))
    assert result.due_rechecks == ["scope-decision"]
    assert result.deferred[0].trigger == "response"
    edit(deferred_home, "cursor.json",
         lambda c: c["deferred"][0].update(checked_response_ref="actual-response"))
    assert not AGENDA.inspect(deferred_home, now=at(15)).due_rechecks


def test_active_work_is_not_silently_replaced_by_due_branch(deferred_home: Path) -> None:
    original = json.loads((SKILL / "assets/cursor.example.json").read_text())
    edit(deferred_home, "cursor.json", lambda c: c.update(frames=original["frames"]))
    result = AGENDA.inspect(deferred_home, now=at(19))
    assert "regression" in result.next_action
    assert result.due_rechecks == ["scope-decision"]
    assert len(result.stack) == 1


def test_whole_lane_steering_is_not_a_clock_or_queue_override(deferred_home: Path) -> None:
    edit(deferred_home, "cursor.json",
         lambda c: c.update(status="steering", next_action="Honor the explicit all-work hold"))
    result = AGENDA.inspect(deferred_home, now=at(19))
    assert result.next_action == "Honor the explicit all-work hold"
    assert result.due_rechecks == ["scope-decision"]


def test_pending_frontier_cannot_appear_complete_or_legacy(deferred_home: Path) -> None:
    edit(deferred_home, "cursor.json", lambda c: c.update(status="complete", queue=[]))
    with pytest.raises(AGENDA.AgendaError, match="complete"):
        AGENDA.inspect(deferred_home, now=at(15))
    edit(deferred_home, "cursor.json", lambda c: c.update(version=1, status="working"))
    with pytest.raises(AGENDA.AgendaError, match="version 2"):
        AGENDA.inspect(deferred_home, now=at(15))


@pytest.mark.parametrize("changes,message", [
    ({"attempted_at": "2026-10-31T14:48:00"}, "timezone"),
    ({"revisit_at": "not-a-date"}, "timestamp"),
    ({"revisit_at": "2026-10-31T14:47:00-07:00"}, "revisit"),
    ({"last_checked_at": "2026-10-31T14:47:00-07:00"}, "revisit"),
    ({"last_checked_at": "2026-10-31T19:00:00-07:00"}, "revisit"),
    ({"frames": []}, "frame chain"),
    ({"saved_revision": 1}, "saved cursor revision"),
    ({"saved_revision": True}, "integer"),
    ({"detours_without_progress": 3}, "detour budget"),
    ({"decision_packet_ref": ""}, "nonempty"),
    ({"response_ref": []}, "nonempty"),
])
def test_invalid_deferred_state_is_a_preserved_diagnostic(
    deferred_home: Path, changes: dict[str, Any], message: str
) -> None:
    edit(deferred_home, "cursor.json", lambda c: c["deferred"][0].update(changes))
    before = (deferred_home / "cursor.json").read_bytes()
    with pytest.raises(AGENDA.AgendaError, match=message):
        AGENDA.inspect(deferred_home, now=at(15))
    assert (deferred_home / "cursor.json").read_bytes() == before


def test_deferred_budget_duplicate_and_stale_definition_refused(deferred_home: Path) -> None:
    original = (deferred_home / "cursor.json").read_text()
    edit(deferred_home, "cursor.json",
         lambda c: c["deferred"].append(deepcopy(c["deferred"][0])))
    with pytest.raises(AGENDA.AgendaError, match="duplicate"):
        AGENDA.inspect(deferred_home, now=at(15))
    edit(deferred_home, "cursor.json", lambda c: c.update(max_deferred=1))
    with pytest.raises(AGENDA.AgendaError, match="branch budget"):
        AGENDA.inspect(deferred_home, now=at(15))
    (deferred_home / "cursor.json").write_text(original)
    edit(deferred_home, "agenda.json", lambda c: c.update(revision=2))
    with pytest.raises(AGENDA.AgendaError, match="agenda revision"):
        AGENDA.inspect(deferred_home, now=at(15))


def test_saved_child_chain_and_return_survive_read_only_inspection(deferred_home: Path) -> None:
    board = json.loads((deferred_home / "agenda.json").read_text())
    board["source"] = {"kind": "task", "ref": "scoped-prerequisite"}
    (deferred_home / "child.json").write_text(json.dumps(board))
    def nested(c: dict[str, Any]) -> None:
        branch = c["deferred"][0]
        child = deepcopy(branch["frames"][0])
        child.update(agenda="child.json", return_to={
            "agenda": "agenda.json", "node": "validate", "next_action": "Revalidate parent",
        })
        branch["frames"].append(child)
    edit(deferred_home, "cursor.json", nested)
    result = AGENDA.inspect(deferred_home, now=at(15))
    assert len(result.deferred[0].stack) == 2
    edit(deferred_home, "cursor.json",
         lambda c: c["deferred"][0]["frames"][-1]["return_to"].update(agenda="child.json"))
    with pytest.raises(AGENDA.AgendaError, match="parent"):
        AGENDA.inspect(deferred_home, now=at(15))


def test_board_reads_are_shared_across_saved_and_current_chains(
    deferred_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit(deferred_home, "cursor.json", lambda c: c.update(queue=["agenda.json"]))
    read = AGENDA._read
    calls: list[str] = []
    def counted(path: Path) -> dict[str, Any]:
        calls.append(path.name)
        return read(path)
    monkeypatch.setattr(AGENDA, "_read", counted)
    AGENDA.inspect(deferred_home, now=at(15))
    assert calls.count("agenda.json") == 1


def test_cli_reports_due_state_without_a_scheduler_or_cursor_write(
    deferred_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (deferred_home / "cursor.json").read_bytes()
    assert AGENDA.main(["next", "--state-home", str(deferred_home), "--json",
                        "--now", "2026-10-31T19:00:00-07:00"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["due_rechecks"] == ["scope-decision"]
    assert before == (deferred_home / "cursor.json").read_bytes()
    assert AGENDA.main(["next", "--state-home", str(deferred_home),
                        "--now", "14:48"]) == 2
    assert "inspection time" in capsys.readouterr().err
