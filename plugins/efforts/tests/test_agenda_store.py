"""Synthetic standalone store, semantic movement and delivery contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "driving-effort-agendas"
sys.path.insert(0, str(SKILL / "scripts"))
AGENDA = importlib.import_module("agenda")
STORE = importlib.import_module("agenda_store")
CLI = importlib.import_module("agenda_cli")
PUBLISH = importlib.import_module("agenda_publish")


def proof(criterion: str = "recorded criterion") -> list[dict[str, str]]:
    return [{"criterion": criterion, "kind": "measured", "ref": "synthetic-test",
             "inputs": "fixture revision"}]


def request(change: dict[str, Any], revision: int = 1, identity: str = "operation") -> dict[str, Any]:
    return {"schema": "efforts.agenda-request", "version": 1, "expected_revision": revision,
            "operation_id": identity, "change": change}


@pytest.fixture
def documents() -> dict[str, Any]:
    board = json.loads((SKILL / "assets" / "agenda.example.json").read_text())
    cursor = json.loads((SKILL / "assets" / "cursor.example.json").read_text())
    board["source"]["ref"] = "fixture-source: contract and validation"
    cursor["worktree"] = "fixture-lane"
    cursor["original_evidence"] = {"future_field": ["preserve", 1]}
    return {"cursor": cursor, "boards": {"agenda.json": board}}


@pytest.fixture
def home(tmp_path: Path, documents: dict[str, Any]) -> Path:
    STORE.mutate(tmp_path, request({"operation": "init", "documents": documents}, 0, "init"))
    return tmp_path


def update(home: Path, change: dict[str, Any]) -> dict[str, Any]:
    revision = STORE.read(home)["revision"]
    return STORE.mutate(home, request(change, revision, f"op-{revision}"))[0]


def outcome(home: Path, name: str) -> None:
    snapshot = STORE.read(home)["snapshot"]
    update(home, {"operation": "outcome", "outcome": name,
                  "evidence": sum((proof(c) for c in snapshot["exit_criteria"]), [])})


def queue(home: Path, name: str = "child.json") -> None:
    board = deepcopy(STORE.read(home)["documents"]["boards"]["agenda.json"])
    board["source"] = {"kind": "task", "ref": name}
    update(home, {"operation": "queue", "agenda": name, "board": board,
                  "assignment_ref": "assigned prerequisite", "evidence": proof()})


def defer(home: Path, identity: str = "decision") -> None:
    update(home, {"operation": "defer", "id": identity, "reason": "approval pending",
                  "decision_ref": "scope decision", "decision_packet_ref": "bounded proposal",
                  "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                  "recheck_action": "Read current permission", "next_action": "Prepare decision",
                  "evidence": proof()})


def test_atomic_init_truthful_unknowns_and_composite_source(home: Path) -> None:
    state = STORE.read(home)
    frame = state["documents"]["cursor"]["frames"][0]
    assert state["revision"] == 1 and frame["entered_at"] is None
    assert frame["occurrence_id"] is None and frame["timing_qualification"] == "unknown"
    assert state["metadata"]["actor"] == {"actor": None, "session": None, "qualification": "unknown"}
    assert "contract and validation" in state["snapshot"]["root"]
    assert state["documents"]["cursor"]["original_evidence"]["future_field"] == ["preserve", 1]
    assert datetime.fromisoformat(state["committed_at"].replace("Z", "+00:00")).tzinfo is not None
    with STORE.transaction(home) as db:
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
        assert db.execute("SELECT revision FROM delivery").fetchone()[0] == 1


def test_import_is_read_only_and_retry_does_not_reread_inputs(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    manual, managed = tmp_path / "manual", tmp_path / "managed"
    manual.mkdir()
    managed.mkdir()
    (manual / "cursor.json").write_text(json.dumps(documents["cursor"]))
    (manual / "agenda.json").write_text(json.dumps(documents["boards"]["agenda.json"]))
    before = {p.name: p.read_bytes() for p in manual.iterdir()}
    req = request({"operation": "init"}, 0, "import")
    result, _ = STORE.mutate(managed, req, import_home=manual)
    assert before == {p.name: p.read_bytes() for p in manual.iterdir()}
    assert not (managed / "cursor.json").exists()
    (manual / "cursor.json").write_text("inputs no longer readable")
    assert STORE.mutate(managed, req, import_home=manual) == (result, False)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX legacy-adoption durability path")
def test_same_home_adoption_retains_original_bytes_and_refuses_legacy(
    tmp_path: Path, documents: dict[str, Any]
) -> None:
    for name, document in {"cursor.json": documents["cursor"], **documents["boards"]}.items():
        (tmp_path / name).write_text(json.dumps(document))
    before = (tmp_path / "cursor.json").read_bytes()
    STORE.mutate(tmp_path, request({"operation": "init"}, 0, "import"), import_home=tmp_path)
    assert json.loads((tmp_path / "cursor.json").read_text())["version"] == 3
    import base64

    assert base64.b64decode(STORE.read(tmp_path)["metadata"]["legacy_fence"]["original_cursor"]) == before
    with pytest.raises(AGENDA.AgendaError, match="managed state"):
        AGENDA.inspect(tmp_path)
    assert STORE.read(tmp_path)["snapshot"]["status"] == "working"


def test_retry_returns_original_result_even_after_new_updates(home: Path) -> None:
    req = request({"operation": "park", "ref": "discovery", "reason": "nonblocking"})
    original, accepted = STORE.mutate(home, req, actor="unverified", session="asserted-session")
    assert accepted and original["actor"]["qualification"] == "caller-asserted"
    update(home, {"operation": "park", "ref": "second", "reason": "also nonblocking"})
    assert STORE.mutate(home, req, actor="unverified", session="asserted-session") == (original, False)
    assert STORE.read(home)["revision"] == 3
    for changed in [request({"operation": "park", "ref": "different", "reason": "nonblocking"}),
                    {**req, "expected_revision": 2}]:
        with pytest.raises(AGENDA.AgendaError, match="mismatched"):
            STORE.mutate(home, changed, actor="unverified", session="asserted-session")


def test_concurrent_writers_one_commit_no_partial_state(home: Path) -> None:
    def write(identity: str) -> bool:
        try:
            STORE.mutate(home, request({"operation": "park", "ref": identity, "reason": "discovery"},
                                      identity=identity))
            return True
        except AGENDA.AgendaError as exc:
            assert "stale expected revision" in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["writer-one", "writer-two"]))
    assert sorted(results) == [False, True]
    state = STORE.read(home)
    assert state["revision"] == 2 and state["snapshot"]["parked"] == 1
    assert len(STORE.history(home)) == 2


def test_transaction_fault_rolls_back_full_commit(home: Path) -> None:
    before = STORE.read(home)
    with sqlite3.connect(home / STORE.DATABASE) as db:
        db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON operations BEGIN "
                   "SELECT RAISE(ABORT,'injected history fault after state insert'); END")
    with pytest.raises(AGENDA.AgendaError, match="injected"):
        update(home, {"operation": "park", "ref": "fault", "reason": "synthetic"})
    assert STORE.read(home) == before
    assert len(STORE.history(home)) == 1


def test_actual_interrupted_writer_recovers_original_state(home: Path) -> None:
    before = STORE.read(home)
    code = """
import os, sqlite3, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agenda_store as s
class Interrupted(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql.startswith("INSERT INTO operations"):
            os._exit(17)
        return super().execute(sql, *args, **kwargs)
original = sqlite3.connect
def connect(*args, **kwargs):
    return original(*args, **kwargs, factory=Interrupted)
s.sqlite3.connect = connect
s.mutate(Path(sys.argv[2]), {
    "schema": "efforts.agenda-request", "version": 1, "expected_revision": 1,
    "operation_id": "crash", "change": {"operation": "park", "ref": "fault", "reason": "fixture"}
})
"""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run([sys.executable, "-c", code, str(SKILL / "scripts"), str(home)],
                            capture_output=True, timeout=10, creationflags=flags)
    assert result.returncode == 17, result.stderr
    assert STORE.read(home) == before
    assert len(STORE.history(home)) == 1


def test_reader_uses_transaction_snapshot_not_uncommitted_writer(home: Path) -> None:
    before = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError, match="synthetic rollback"):
        with STORE.transaction(home, write=True) as db:
            db.execute("UPDATE state SET revision=999")
            db.execute("DELETE FROM delivery")
            assert STORE.read(home) == before
            raise AGENDA.AgendaError("synthetic rollback")
    assert STORE.read(home) == before


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":1}', b'{"value":NaN}', b'{"value":Infinity}',
    b'{"value":1e999}', b'{"value":' + b'"x"' * 70000 + b'}'])
def test_strict_bounded_json(raw: bytes) -> None:
    with pytest.raises(AGENDA.AgendaError):
        AGENDA.decode(raw)


@pytest.mark.parametrize("mutate_request", [
    lambda r: r.update(version=True), lambda r: r.update(version=2),
    lambda r: r.update(schema="unknown"), lambda r: r.update(extra="ignored"),
    lambda r: r.update(expected_revision=True), lambda r: r.update(operation_id=""),
    lambda r: r["change"].update(cursor={}), lambda r: r["change"].update(operation="overwrite"),
])
def test_strict_requests_have_no_partial_effect(
    home: Path, mutate_request: Any
) -> None:
    before = STORE.read(home)
    req = request({"operation": "park", "ref": "fixture", "reason": "nonblocking"})
    mutate_request(req)
    with pytest.raises(AGENDA.AgendaError):
        STORE.mutate(home, req)
    assert STORE.read(home) == before


@pytest.mark.parametrize("location", ["root", "ancestor", "database", "journal", "board"])
def test_symlink_paths_refused(home: Path, tmp_path: Path, location: str) -> None:
    link = tmp_path / "alias"
    if location in ("root", "ancestor"):
        link.symlink_to(home, target_is_directory=True)
        with pytest.raises(AGENDA.AgendaError, match="symlink"):
            STORE.read(link if location == "root" else link / "descendant")
    elif location == "database":
        target = tmp_path / "other"
        target.mkdir()
        (target / STORE.DATABASE).symlink_to(home / STORE.DATABASE)
        with pytest.raises(AGENDA.AgendaError, match="symlink"):
            STORE.read(target)
    elif location == "journal":
        (home / (STORE.DATABASE + "-journal")).symlink_to(tmp_path / "missing")
        with pytest.raises(AGENDA.AgendaError, match="symlink"):
            STORE.read(home)
    else:
        (home / "agenda.json").symlink_to(tmp_path / "missing")
        with pytest.raises(AGENDA.AgendaError, match="symlink"):
            STORE.read(home)


@pytest.mark.parametrize("name", ["../escape.json", "/absolute.json", "cursor.json",
                                "agenda.sqlite3", "sub/../alias.json", "./alias.json", "nul\x00.json"])
def test_board_paths_refused(home: Path, name: str) -> None:
    before = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError):
        queue(home, name)
    assert STORE.read(home) == before


def test_every_queued_board_validated_not_only_first(home: Path) -> None:
    state = STORE.read(home)
    bad = deepcopy(state["documents"])
    bad["boards"]["bad.json"] = deepcopy(bad["boards"]["agenda.json"])
    bad["boards"]["bad.json"]["nodes"]["implement"]["on"]["ready"] = "missing"
    bad["cursor"]["queue"] = ["agenda.json", "bad.json"]
    with pytest.raises(AGENDA.AgendaError, match="destination"):
        STORE.validate_documents(home, bad)


def test_outcomes_preserve_evidence_and_finite_visits(home: Path) -> None:
    with pytest.raises(AGENDA.AgendaError, match="declared edge"):
        update(home, {"operation": "outcome", "outcome": "made-up", "evidence": proof()})
    with pytest.raises(AGENDA.AgendaError, match="every exit"):
        update(home, {"operation": "outcome", "outcome": "ready", "evidence": proof()})
    for _ in range(2):
        outcome(home, "ready")
        outcome(home, "failed")
    outcome(home, "ready")
    before = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError, match="visit budget"):
        outcome(home, "failed")
    assert STORE.read(home) == before
    frame = before["documents"]["cursor"]["frames"][0]
    assert len(frame["evidence"]) == 5
    assert frame["entered_at"] and frame["occurrence_id"] and frame["timing_qualification"] == "generated"


def test_refinement_preserves_sources_nodes_evidence_and_tighter_limits(home: Path) -> None:
    old = STORE.read(home)["documents"]["boards"]["agenda.json"]
    for mutation in [lambda b: b["source"].update(ref="other"),
                     lambda b: b["nodes"].pop("implement"),
                     lambda b: b["nodes"]["implement"].update(max_visits=4),
                     lambda b: b["nodes"]["implement"].update(exit=["discard original"])]:
        board = deepcopy(old)
        mutation(board)
        with pytest.raises(AGENDA.AgendaError):
            update(home, {"operation": "refine", "agenda": "agenda.json", "board": board,
                          "evidence": proof()})
    board = deepcopy(old)
    board["revision"] = 2
    board["nodes"]["implement"]["action"] = "Refined implementation"
    board["nodes"]["implement"]["max_visits"] = 2
    update(home, {"operation": "refine", "agenda": "agenda.json", "board": board, "evidence": proof()})
    assert STORE.read(home)["documents"]["cursor"]["frames"][0]["agenda_revision"] == 2
    update(home, {"operation": "limits", "limits": {"max_depth": 2}, "evidence": proof()})
    with pytest.raises(AGENDA.AgendaError, match="loosen"):
        update(home, {"operation": "limits", "limits": {"max_depth": 3}, "evidence": proof()})


def test_child_return_preserves_root_and_requires_actual_acceptance(home: Path) -> None:
    queue(home)
    update(home, {"operation": "push", "agenda": "child.json", "assignment_ref": "accepted child",
        "reason": "required parser prerequisite", "return_node": "validate",
        "return_action": "Revalidate original parent", "evidence": proof()})
    state = STORE.read(home)
    assert len(state["snapshot"]["stack"]) == 2 and "fixture-source" in state["snapshot"]["root"]
    with pytest.raises(AGENDA.AgendaError, match="terminal"):
        update(home, {"operation": "return", "acceptance_ref": "child gate", "evidence": proof()})
    outcome(home, "ready")
    outcome(home, "passed")
    criterion = STORE.read(home)["snapshot"]["exit_criteria"][0]
    update(home, {"operation": "return", "acceptance_ref": "child canonical acceptance",
                  "evidence": proof(criterion)})
    state = STORE.read(home)
    assert len(state["snapshot"]["stack"]) == 1
    assert state["snapshot"]["next_action"] == "Revalidate original parent"
    assert state["documents"]["cursor"]["child_results"][0]["acceptance_ref"] == "child canonical acceptance"
    assert state["documents"]["cursor"]["detours_without_progress"] == 1


def test_deferred_recheck_restore_is_deliberate_and_budgeted(home: Path) -> None:
    before = deepcopy(STORE.read(home)["documents"]["cursor"]["frames"])
    defer(home)
    branch = STORE.read(home)["documents"]["cursor"]["deferred"][0]
    assert branch["frames"][0]["visits"] == before[0]["visits"]
    assert branch["frames"][0]["return_to"] is None
    with pytest.raises(AGENDA.AgendaError, match="eligible recheck"):
        update(home, {"operation": "restore", "id": "decision", "acceptance_ref": "approved",
                      "evidence": proof()})
    with pytest.raises(AGENDA.AgendaError, match="not due"):
        update(home, {"operation": "recheck", "id": "decision",
                      "revisit_at": branch["revisit_at"], "evidence": proof()})
    update(home, {"operation": "response", "id": "decision", "response_ref": "actual response"})
    assert STORE.read(home)["snapshot"]["due_rechecks"] == ["decision"]
    update(home, {"operation": "recheck", "id": "decision", "revisit_at": branch["revisit_at"],
                  "evidence": proof()})
    assert STORE.read(home)["snapshot"]["due_rechecks"] == []
    update(home, {"operation": "restore", "id": "decision", "acceptance_ref": "requalified approval",
                  "evidence": proof()})
    state = STORE.read(home)
    assert state["documents"]["cursor"]["frames"][0]["visits"] == before[0]["visits"]
    assert state["documents"]["cursor"]["detours_without_progress"] == 1
    assert state["documents"]["cursor"]["restored_branches"][0]["branch"]["checks"]


def test_wait_and_link_are_evidence_preserving_no_execution(home: Path) -> None:
    update(home, {"operation": "link", "executions": [{"kind": "child-task", "ref": "fixture-task"}],
                  "evidence": proof()})
    update(home, {"operation": "wait", "reason": "external review", "next_action": "Await verdict",
                  "evidence": proof()})
    state = STORE.read(home)
    assert state["snapshot"]["status"] == "waiting" and state["snapshot"]["next_action"] == "Await verdict"
    assert state["documents"]["cursor"]["frames"][0]["executions"][0]["ref"] == "fixture-task"


def test_complete_root_select_and_block_require_preserved_acceptance(home: Path) -> None:
    queue(home)
    outcome(home, "ready")
    outcome(home, "passed")
    update(home, {"operation": "complete-root", "acceptance_ref": "canonical source accepted",
                  "evidence": proof(STORE.read(home)["snapshot"]["exit_criteria"][0])})
    update(home, {"operation": "select", "agenda": "child.json", "assignment_ref": "queued assignment"})
    update(home, {"operation": "block", "reason": "scope question", "next_action": "Prepare decision",
                  "evidence": proof()})
    state = STORE.read(home)
    assert state["snapshot"]["status"] == "steering" and state["snapshot"]["queued"] == []
    assert state["documents"]["cursor"]["completed_roots"][0]["frames"][0]["visits"] == {
        "implement": 1, "validate": 1, "reconcile": 1}


def test_depth_deferred_and_restore_detour_guards(home: Path) -> None:
    update(home, {"operation": "limits", "limits": {"max_depth": 1, "max_deferred": 1,
                  "max_detours_without_progress": 1}, "evidence": proof()})
    queue(home)
    before = STORE.read(home)
    with pytest.raises(AGENDA.AgendaError, match="depth budget"):
        update(home, {"operation": "push", "agenda": "child.json", "assignment_ref": "assigned",
            "reason": "prerequisite", "return_node": "validate", "return_action": "Revalidate",
            "evidence": proof()})
    assert STORE.read(home) == before
    defer(home)
    update(home, {"operation": "select", "agenda": "child.json", "assignment_ref": "assigned"})
    with pytest.raises(AGENDA.AgendaError, match="branch budget"):
        defer(home, "second")
    outcome(home, "ready")
    outcome(home, "passed")
    update(home, {"operation": "complete-root", "acceptance_ref": "child accepted",
                  "evidence": proof(STORE.read(home)["snapshot"]["exit_criteria"][0])})
    for cycle in range(2):
        update(home, {"operation": "response", "id": "decision", "response_ref": str(cycle)})
        branch = STORE.read(home)["documents"]["cursor"]["deferred"][0]
        update(home, {"operation": "recheck", "id": "decision", "revisit_at": branch["revisit_at"],
                      "evidence": proof()})
        change = {"operation": "restore", "id": "decision", "acceptance_ref": "requalified",
                  "evidence": proof()}
        if cycle == 0:
            update(home, change)
            defer(home)
        else:
            before = STORE.read(home)
            with pytest.raises(AGENDA.AgendaError, match="detour budget"):
                update(home, change)
            assert STORE.read(home) == before


def test_parent_progress_is_explicit_evidence_not_automatic_reset(home: Path) -> None:
    defer(home)
    update(home, {"operation": "response", "id": "decision", "response_ref": "response"})
    branch = STORE.read(home)["documents"]["cursor"]["deferred"][0]
    update(home, {"operation": "recheck", "id": "decision", "revisit_at": branch["revisit_at"],
                  "evidence": proof()})
    update(home, {"operation": "restore", "id": "decision", "acceptance_ref": "requalified",
                  "evidence": proof()})
    outcome(home, "ready")
    assert STORE.read(home)["documents"]["cursor"]["detours_without_progress"] == 1
    update(home, {"operation": "progress", "reason": "Parent regression now resolved",
                  "evidence": proof()})
    cursor = STORE.read(home)["documents"]["cursor"]
    assert cursor["detours_without_progress"] == 0 and cursor["progress_decisions"][0]["previous_detours"] == 1


def test_import_deferred_frontier_preserves_older_revision_limits_and_unknown_times(tmp_path: Path) -> None:
    cursor = json.loads((SKILL / "assets" / "cursor.deferred.example.json").read_text())
    board = json.loads((SKILL / "assets" / "agenda.deferred.example.json").read_text())
    result, _ = STORE.mutate(tmp_path, request({"operation": "init", "documents": {
        "cursor": cursor, "boards": {"agenda.json": board}}}, 0, "init-deferred"))
    state = STORE.read(tmp_path)
    branch = state["documents"]["cursor"]["deferred"][0]
    assert result["revision"] == 3 and branch["saved_revision"] == 1
    assert branch["max_depth"] == cursor["deferred"][0]["max_depth"]
    assert branch["attempted_at"] == cursor["deferred"][0]["attempted_at"]
    assert branch["frames"][0]["entered_at"] is None
    assert state["documents"]["cursor"]["max_deferred"] == 8


def test_postcommit_delivery_record_failure_still_returns_persisted_result(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = home / "request.json"
    path.write_text(json.dumps(request({"operation": "park", "ref": "fixture", "reason": "nonblocking"})))

    def fail(*args: Any) -> None:
        raise AGENDA.AgendaError("synthetic postcommit record failure")

    monkeypatch.setattr(CLI, "publish", fail)
    assert CLI.main(["update", "--state-home", str(home), "--request", str(path),
                     "--publish-arg", sys.executable]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["result"]["persisted"] is True and output["delivery"]["state"] == "pending"
    assert STORE.read(home)["revision"] == 2


def test_history_pagination_retains_all_accepted_records(home: Path) -> None:
    for index in range(102):
        update(home, {"operation": "park", "ref": str(index), "reason": "synthetic"})
    latest = STORE.history(home)
    older = STORE.history(home, before_revision=latest[-1]["revision"])
    assert len(latest) == 100 and len(older) == 3
    assert older[-1]["operation_id"] == "init"
    with pytest.raises(AGENDA.AgendaError, match="<= 100"):
        STORE.history(home, 101)


def test_publisher_receives_only_committed_snapshot_and_delivery_retry_is_separate(home: Path) -> None:
    req = request({"operation": "park", "ref": "publish", "reason": "nonblocking"})
    original, _ = STORE.mutate(home, req, publisher_configured=True, session="asserted")
    argv = [sys.executable, "-c",
        "import json,sqlite3,sys; o=json.load(sys.stdin); "
        "c=sqlite3.connect(sys.argv[1]); "
        "assert c.execute('select revision from state').fetchone()[0]==o['revision']; "
        "assert o['schema']=='efforts.agenda-observation' and o['version']==1; "
        "assert o['source']['session']=='asserted' and o['snapshot']['parked']==1; "
        "print(json.dumps({'ok':True,'observed':o['revision']}))", str(home / STORE.DATABASE)]
    assert PUBLISH.publish(home, argv)["state"] == "delivered"
    assert STORE.mutate(home, req, publisher_configured=True, session="asserted") == (original, False)
    assert original["delivery"]["state"] == "pending"
    assert PUBLISH.publish(home, argv)["state"] == "delivered"
    assert STORE.read(home)["revision"] == 2 and len(STORE.history(home)) == 2


@pytest.mark.parametrize("code, expected", [
    ("print('{\"ok\":true}')", "delivered"),
    ("print('{\"ok\":false,\"error\":{\"code\":\"unsupported\",\"message\":\"not installed\"}}')", "pending"),
    ("print('{}')", "pending"), ("print('not JSON')", "pending"),
    ("print('x'*35841)", "pending"), ("raise SystemExit(4)", "pending"),
])
def test_bounded_publisher_replies(home: Path, code: str, expected: str) -> None:
    status = PUBLISH.publish(home, [sys.executable, "-c", code])
    assert status["state"] == expected
    assert STORE.read(home)["delivery"] == status
    assert STORE.read(home)["revision"] == 1


def test_offline_not_configured_superseded_and_large_observation(home: Path) -> None:
    assert PUBLISH.publish(home, [])["state"] == "not-configured"
    assert PUBLISH.publish(home, [str(home / "missing-owned-adapter")])["state"] == "pending"
    revision, _ = STORE.delivery_record(home)
    update(home, {"operation": "park", "ref": "newer", "reason": "nonblocking"})
    assert STORE.record_delivery(home, revision, {"state": "delivered"})["error"]["code"] == "superseded"
    assert STORE.read(home)["delivery"]["state"] == "not-configured"
    update(home, {"operation": "wait", "reason": "fixture", "next_action": "x" * 33000,
                  "evidence": proof()})
    status = PUBLISH.publish(home, [str(home / "must-not-execute")])
    assert status["error"]["code"] == "observation-too-large"


def test_cli_and_store_version_diagnostics(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert CLI.main(["next", "--state-home", str(home), "--expected-revision", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["worktree"] == "fixture-lane"
    assert CLI.main(["read", "--state-home", str(home), "--expected-revision", "0"]) == 2
    assert "revision changed" in capsys.readouterr().err
    with sqlite3.connect(home / STORE.DATABASE) as db:
        db.execute("PRAGMA user_version=99")
    assert CLI.main(["read", "--state-home", str(home)]) == 2
    assert "unsupported" in capsys.readouterr().err
