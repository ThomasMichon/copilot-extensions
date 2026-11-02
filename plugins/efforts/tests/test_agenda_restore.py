"""Shared-root restoration through the real CLI and transactional store."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from test_agenda_store import AGENDA, CLI, SKILL, STORE, proof, request


def future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def submit(home: Path, capsys: pytest.CaptureFixture[str], change: dict[str, Any]) -> dict[str, Any]:
    revision = STORE.read(home)["revision"]
    path = home / "request.json"
    path.write_text(json.dumps(request(change, revision, f"restore-test-{revision}")))
    assert CLI.main(["update", "--state-home", str(home), "--request", str(path)]) == 0
    return json.loads(capsys.readouterr().out)["result"]


@pytest.fixture
def shipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    initialize(tmp_path, capsys)
    return tmp_path


def initialize(
    home: Path, capsys: pytest.CaptureFixture[str], cursor: dict[str, Any] | None = None,
    board: dict[str, Any] | None = None,
) -> None:
    documents = {
        "cursor": cursor if cursor is not None else json.loads(
            (SKILL / "assets" / "cursor.deferred.example.json").read_text()),
        "boards": {"agenda.json": board if board is not None else json.loads(
            (SKILL / "assets" / "agenda.deferred.example.json").read_text())},
    }
    path = home / "init.json"
    path.write_text(json.dumps(request({"operation": "init", "documents": documents}, 0, "shipped-init")))
    assert CLI.main(["init", "--state-home", str(home), "--request", str(path)]) == 0
    capsys.readouterr()


def advance(home: Path, capsys: pytest.CaptureFixture[str], outcome: str) -> None:
    snapshot = STORE.read(home)["snapshot"]
    submit(home, capsys, {"operation": "outcome", "outcome": outcome,
        "evidence": [item for criterion in snapshot["exit_criteria"]
                     for item in proof(criterion)]})


def park_chain(home: Path, capsys: pytest.CaptureFixture[str], identity: str) -> None:
    submit(home, capsys, {"operation": "defer", "id": identity, "reason": "synthetic pending decision",
        "decision_ref": identity, "decision_packet_ref": f"{identity}-packet",
        "revisit_at": future(), "recheck_action": "Inspect current conditions",
        "next_action": "Preserve pending roots", "evidence": proof(f"defer-{identity}")})


def restore(home: Path, capsys: pytest.CaptureFixture[str], identity: str) -> dict[str, Any]:
    submit(home, capsys, {"operation": "response", "id": identity, "response_ref": f"{identity}-response"})
    submit(home, capsys, {"operation": "recheck", "id": identity,
        "revisit_at": future(), "evidence": proof(f"recheck-{identity}")})
    return submit(home, capsys, {"operation": "restore", "id": identity,
        "acceptance_ref": f"{identity}-acceptance", "evidence": proof(f"restore-{identity}")})


def test_shipped_restore_retains_intervening_shared_root_visits_and_evidence(
    shipped: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for outcome in ("packet_ready", "recheck_ready", "approved"):
        advance(shipped, capsys, outcome)
    park_chain(shipped, capsys, "continuing-work")
    before = STORE.read(shipped)["documents"]["cursor"]
    continuing = deepcopy(before["deferred"][-1]["frames"][0])
    assert continuing["visits"]["qualify"] == continuing["visits"]["apply"] == 2
    restore(shipped, capsys, "capacity-effect-approval")
    after = STORE.read(shipped)["documents"]["cursor"]
    active = after["frames"][0]
    assert active["visits"] == continuing["visits"], (
        f"restoration reused consumed shared-root budget: {continuing['visits']} -> {active['visits']}")
    assert all(item in active["evidence"] for item in continuing["evidence"])
    assert after["deferred"][0]["id"] == "continuing-work"


def test_shared_restore_keeps_consumed_limits_and_original_idempotent_result(
    shipped: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for outcome in ("packet_ready", "recheck_ready", "approved"):
        advance(shipped, capsys, outcome)
    park_chain(shipped, capsys, "continuing-work")
    accepted = restore(shipped, capsys, "capacity-effect-approval")
    accepted_request = json.loads((shipped / "request.json").read_text())
    for outcome in ("blocked", "packet_ready", "recheck_ready", "approved"):
        advance(shipped, capsys, outcome)
    park_chain(shipped, capsys, "second-current")
    restore(shipped, capsys, "continuing-work")
    current = STORE.read(shipped)
    assert current["documents"]["cursor"]["frames"][0]["visits"]["apply"] == 3
    assert STORE.mutate(shipped, accepted_request) == (accepted, False)
    advance(shipped, capsys, "blocked")
    advance(shipped, capsys, "packet_ready")
    advance(shipped, capsys, "recheck_ready")
    before = STORE.read(shipped)
    with pytest.raises(AGENDA.AgendaError, match="visit budget"):
        STORE.mutate(shipped, request({"operation": "outcome", "outcome": "approved",
            "evidence": proof(before["snapshot"]["exit_criteria"][0])},
            before["revision"], "spent-apply-budget"))
    assert STORE.read(shipped) == before
    park_chain(shipped, capsys, "third-current")
    submit(shipped, capsys, {"operation": "response", "id": "second-current", "response_ref": "response"})
    submit(shipped, capsys, {"operation": "recheck", "id": "second-current",
        "revisit_at": future(), "evidence": proof()})
    before = STORE.read(shipped)
    with pytest.raises(AGENDA.AgendaError, match="detour budget"):
        STORE.mutate(shipped, request({"operation": "restore", "id": "second-current",
            "acceptance_ref": "synthetic-acceptance", "evidence": proof()},
            before["revision"], "spent-detour-budget"))
    assert STORE.read(shipped) == before


def fork_cursor(*, tracked: bool) -> dict[str, Any]:
    cursor = json.loads((SKILL / "assets" / "cursor.deferred.example.json").read_text())
    cursor.update(frames=[], status="steering")
    baseline = {"qualify": 1, "apply": 1, "research": 1}
    first = deepcopy(cursor["deferred"][0])
    second = deepcopy(first)
    first["id"], second["id"] = "fork-a", "fork-b"
    for item in (first, second):
        item["frames"][0].update(node="qualify", next_action="Requalify scope")
    first["frames"][0].update(visits={**baseline, "qualify": 2}, evidence=proof("branch-a"))
    second["frames"][0].update(visits={**baseline, "apply": 2}, evidence=proof("branch-b"))
    if tracked:
        first["frames"][0].update(frame_id="shared-root", visit_origin=baseline,
            visit_log=[{"id": "entry-a", "node": "qualify"}])
        second["frames"][0].update(
            node="qualify", visits={**baseline, "qualify": 2, "apply": 2},
            frame_id="shared-root", visit_origin=baseline,
            visit_log=[{"id": "entry-b-apply", "node": "apply"}, {"id": "entry-b", "node": "qualify"}])
    else:
        second["frames"][0].update(node="apply")
    cursor["deferred"] = [first, second]
    return cursor


def test_tracked_fork_counts_distinct_entries_not_componentwise_max(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    initialize(tmp_path, capsys, fork_cursor(tracked=True))
    restore(tmp_path, capsys, "fork-a")
    frame = STORE.read(tmp_path)["documents"]["cursor"]["frames"][0]
    assert frame["visits"] == {"qualify": 3, "apply": 2, "research": 1}
    assert {"entry-a", "entry-b", "entry-b-apply"} == {event["id"] for event in frame["visit_log"]}
    assert proof("branch-a")[0] in frame["evidence"] and proof("branch-b")[0] in frame["evidence"]


def test_incomparable_legacy_fork_refuses_without_discarding_pending_chains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    initialize(tmp_path, capsys, fork_cursor(tracked=False))
    submit(tmp_path, capsys, {"operation": "response", "id": "fork-a", "response_ref": "response"})
    submit(tmp_path, capsys, {"operation": "recheck", "id": "fork-a",
        "revisit_at": future(), "evidence": proof()})
    before = STORE.read(tmp_path)
    accepted_count = len(STORE.history(tmp_path))
    with pytest.raises(AGENDA.AgendaError, match="incomparable"):
        STORE.mutate(tmp_path, request({"operation": "restore", "id": "fork-a",
            "acceptance_ref": "acceptance", "evidence": proof()}, before["revision"], "ambiguous"))
    assert STORE.read(tmp_path) == before
    assert len(STORE.history(tmp_path)) == accepted_count


def test_nested_restore_and_child_return_keep_parent_progress_and_stricter_limits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = json.loads((SKILL / "assets" / "cursor.deferred.example.json").read_text())
    cursor["max_detours_without_progress"] = 5
    cursor["deferred"][0].update(max_depth=2, max_detours_without_progress=3)
    initialize(tmp_path, capsys, cursor)
    child = {"version": 1, "source": {"kind": "task", "ref": "child-source"}, "entry": "done",
        "nodes": {"done": {"source_ref": "Child acceptance", "action": "Reconcile child",
            "max_visits": 1, "exit": ["Child accepted"], "on": {}, "terminal": True}}}
    submit(tmp_path, capsys, {"operation": "queue", "agenda": "child.json", "board": child,
        "assignment_ref": "necessary prerequisite", "evidence": proof()})
    submit(tmp_path, capsys, {"operation": "push", "agenda": "child.json",
        "assignment_ref": "necessary prerequisite", "reason": "required",
        "return_node": "qualify", "return_action": "Revalidate parent", "evidence": proof()})
    park_chain(tmp_path, capsys, "child-chain")
    restore(tmp_path, capsys, "capacity-effect-approval")
    for outcome in ("blocked", "packet_ready", "recheck_ready", "approved"):
        advance(tmp_path, capsys, outcome)
    park_chain(tmp_path, capsys, "parent-progress")
    parent = deepcopy(STORE.read(tmp_path)["documents"]["cursor"]["deferred"][-1]["frames"][0])
    restore(tmp_path, capsys, "child-chain")
    active = STORE.read(tmp_path)["documents"]["cursor"]
    assert active["frames"][0]["visits"] == parent["visits"]
    assert active["max_depth"] == 2 and active["max_detours_without_progress"] == 3
    submit(tmp_path, capsys, {"operation": "return", "acceptance_ref": "child-accepted",
        "evidence": proof("Child accepted")})
    after = STORE.read(tmp_path)["documents"]["cursor"]
    assert after["frames"][0]["visits"]["qualify"] == 3
    assert all(item in after["frames"][0]["evidence"] for item in parent["evidence"])
    assert after["frames"][0]["next_action"] == "Revalidate parent"
    assert after["deferred"][0]["id"] == "parent-progress"
    assert after["child_results"][0]["frames"][-1]["agenda"] == "child.json"


def test_distinct_pending_root_never_contributes_visits_or_evidence(
    shipped: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = deepcopy(STORE.read(shipped)["documents"]["boards"]["agenda.json"])
    other["source"]["ref"] = "other-assignment"
    submit(shipped, capsys, {"operation": "queue", "agenda": "other.json", "board": other,
        "assignment_ref": "another assigned root", "evidence": proof()})
    park_chain(shipped, capsys, "original-root")
    submit(shipped, capsys, {"operation": "select", "agenda": "other.json", "assignment_ref": "assigned"})
    advance(shipped, capsys, "pending")
    advance(shipped, capsys, "packet_ready")
    submit(shipped, capsys, {"operation": "wait", "reason": "other root gate",
        "next_action": "Wait for other root", "evidence": proof("other-root-only")})
    park_chain(shipped, capsys, "other-root")
    restore(shipped, capsys, "capacity-effect-approval")
    after = STORE.read(shipped)["documents"]["cursor"]
    assert after["frames"][0]["agenda"] == "agenda.json"
    assert after["frames"][0]["visits"] == {"qualify": 1, "apply": 1, "research": 1}
    assert proof("other-root-only")[0] not in after["frames"][0]["evidence"]
    assert {item["id"] for item in after["deferred"]} == {"original-root", "other-root"}


@pytest.mark.parametrize("identity", ["assigned-root-a", "legacy"])
def test_distinct_identified_roots_can_share_one_board_without_merging(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], identity: str
) -> None:
    cursor = fork_cursor(tracked=True)
    cursor["deferred"][0]["frames"][0]["frame_id"] = identity
    cursor["deferred"][1]["frames"][0]["frame_id"] = "assigned-root-b"
    initialize(tmp_path, capsys, cursor)
    restore(tmp_path, capsys, "fork-a")
    frame = STORE.read(tmp_path)["documents"]["cursor"]["frames"][0]
    assert frame["visits"] == {"qualify": 2, "apply": 1, "research": 1}
    assert proof("branch-b")[0] not in frame["evidence"]
    assert STORE.read(tmp_path)["documents"]["cursor"]["deferred"][0]["id"] == "fork-b"


@pytest.mark.parametrize("fault", ["conflicting-occurrence", "mixed-origin", "unrecorded-visits"])
def test_invalid_required_occurrence_history_cannot_be_accepted(tmp_path: Path, fault: str) -> None:
    cursor = fork_cursor(tracked=True)
    second = cursor["deferred"][1]["frames"][0]
    if fault == "conflicting-occurrence":
        second["visit_log"][0]["id"] = "entry-a"
    elif fault == "mixed-origin":
        second["visit_origin"] = deepcopy(second["visit_origin"])
        second["visit_origin"]["qualify"] = 2
    else:
        second["visits"]["apply"] = 3
    board = json.loads((SKILL / "assets" / "agenda.deferred.example.json").read_text())
    with pytest.raises(AGENDA.AgendaError, match="conflicting|origins differ|unrecorded"):
        STORE.mutate(tmp_path, request({"operation": "init",
            "documents": {"cursor": cursor, "boards": {"agenda.json": board}}}, 0, "bad-history"))
    with pytest.raises(AGENDA.AgendaError, match="uninitialized|no accepted"):
        STORE.read(tmp_path)


def test_tracked_fork_exceeding_shared_budget_is_refused_before_acceptance(tmp_path: Path) -> None:
    cursor = fork_cursor(tracked=True)
    board = json.loads((SKILL / "assets" / "agenda.deferred.example.json").read_text())
    board["nodes"]["qualify"]["max_visits"] = 2
    with pytest.raises(AGENDA.AgendaError, match="shared-root node visit budget"):
        STORE.mutate(tmp_path, request({"operation": "init",
            "documents": {"cursor": cursor, "boards": {"agenda.json": board}}}, 0, "spent-shared-budget"))
    assert not (tmp_path / "cursor.json").exists()
