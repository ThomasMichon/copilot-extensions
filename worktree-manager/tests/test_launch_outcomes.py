"""Manager boundary outcomes are bounded evidence, never accepted-turn verdicts."""

from __future__ import annotations

import json
import sqlite3

import pytest

from worktree_manager import launch_attempts, launch_history_cli
from worktree_manager.launch_attempts import LaunchAttemptError, LaunchAttempts


@pytest.fixture
def store(tmp_path):
    value = LaunchAttempts(tmp_path)
    value.record_intent(
        "a" * 32, action="new", project="example", surface="test", is_local=True,
        prompt="PRIVATE fixture text",
    )
    return value


@pytest.mark.parametrize(("phase", "details", "expected"), [
    ("confirmed", {}, "unknown"),
    ("resolution-requested", {}, "unknown"),
    ("resolved", {"worktree_id": "created-tree"}, "unknown"),
    ("launch-requested", {}, "unknown"),
    ("allocated", {"worktree_id": "created-tree"}, "unknown"),
    ("copilot-started", {}, "unknown"),
    ("prompt-handoff", {}, "unknown"),
    ("target-admitted", {}, "unknown"),
    ("restore-returned", {}, "unknown"),
    ("embodiment-returned", {"worktree_id": "created-tree"}, "unknown"),
    ("unknown", {"code": "engine-error"}, "unknown"),
    ("unknown", {"code": "transport-error"}, "unknown"),
    ("unknown", {"code": "interrupted"}, "interrupted"),
    ("refused", {"code": "unsupported"}, "refused"),
    ("launcher-returned", {"exit_code": 0}, "returned"),
    ("launcher-returned", {"exit_code": 1}, "failed"),
    ("launcher-returned", {"exit_code": -15}, "failed"),
    ("launcher-returned", {}, "unknown"),
])
def test_list_and_detail_have_same_manager_scope_independent_of_event_page(
    store, phase, details, expected,
):
    attempt = "a" * 32
    if phase != "confirmed":
        store.append(attempt, owner="manager", phase=phase, **details)
    before = store.path.read_bytes()
    listed = store.entries()[0]
    detail = store.detail(attempt, event_limit=1)
    after_page = store.detail(attempt, event_limit=1, after_sequence=1000)
    assert after_page["events"] == []
    for row in (listed, detail, after_page):
        assert row["manager_outcome_scope"] == "manager-boundary"
        assert row["manager_outcome"] == expected
        assert row["last_manager_event"]["phase"] == phase
        assert row["last_manager_event"] == listed["last_manager_event"]
        assert "PRIVATE" not in json.dumps(row)
    assert store.entries(outcome=expected)[0]["attempt_id"] == attempt
    assert store.entries(outcome="refused" if expected != "refused" else "unknown") == []
    assert store.path.read_bytes() == before


def test_foreign_owner_events_never_replace_manager_observation(store):
    attempt = "a" * 32
    store.append(attempt, owner="manager", phase="unknown", code="interrupted")
    for owner in ("engine", "daemon", "native"):
        store.append(attempt, owner=owner, phase="launcher-returned", exit_code=0)
    assert store.entries(outcome="interrupted")[0]["last_manager_event"]["sequence"] == 2
    assert store.detail(attempt)["manager_outcome"] == "interrupted"
    assert store.entries(outcome="returned") == []


def test_later_manager_boundary_supersedes_prior_failure_without_claiming_success(store):
    store.append("a" * 32, owner="manager", phase="launcher-returned", exit_code=1)
    store.append("a" * 32, owner="manager", phase="launch-requested")
    assert store.entries()[0]["manager_outcome"] == "unknown"
    assert store.entries(outcome="failed") == []
    assert store.detail("a" * 32)["events"][1]["details"] == {"exit_code": 1}


def test_missing_manager_event_is_unknown_not_inferred_crash(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM events")
    row = store.entries(outcome="unknown")[0]
    assert row["manager_outcome"] == "unknown"
    assert row["last_manager_event"] is None
    assert store.detail("a" * 32)["last_manager_event"] is None


def test_filter_is_applied_before_limit_and_equal_time_pagination(store, monkeypatch):
    monkeypatch.setattr(launch_attempts, "_timestamp", lambda: 1234)
    for index in range(9):
        attempt = f"{index:032x}"
        store.record_intent(attempt, action="open", project="example", surface="test", is_local=True)
        store.append(
            attempt, owner="manager", phase="launcher-returned",
            exit_code=1 if index % 2 else 0,
        )
    page = store.entries(outcome="failed", limit=2)
    assert [row["attempt_id"] for row in page] == [f"{n:032x}" for n in (7, 5)]
    last = page[-1]
    rest = store.entries(
        outcome="failed", limit=2, before=last["submitted_at"], before_id=last["attempt_id"],
    )
    assert [row["attempt_id"] for row in rest] == [f"{n:032x}" for n in (3, 1)]
    assert store.entries(outcome="failed", project="other") == []


@pytest.mark.parametrize("outcome", ["success", "accepted", "", 1, [], {}])
def test_invalid_filter_is_explicit_even_without_storage(tmp_path, outcome):
    store = LaunchAttempts(tmp_path)
    with pytest.raises(LaunchAttemptError, match="metadata"):
        store.entries(outcome=outcome)
    assert not store.path.exists()


def test_corrupt_latest_event_is_not_presented_as_a_valid_outcome(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE events SET phase='launcher-returned', details=?",
            (json.dumps({"exit_code": True}),),
        )
    for read in (store.entries, lambda: store.detail("a" * 32)):
        with pytest.raises(LaunchAttemptError):
            read()


def test_cli_filter_never_queries_engine_and_detail_rejects_list_filter(store, monkeypatch, capsys):
    monkeypatch.setattr(launch_history_cli, "LaunchAttempts", lambda: store)
    monkeypatch.setattr(
        launch_history_cli.engine_client, "run_json",
        lambda *args, **kwargs: pytest.fail("outcome list must remain local"),
    )
    store.append("a" * 32, owner="manager", phase="launcher-returned", exit_code=0)
    assert launch_history_cli.main(["--json", "--outcome", "returned"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["attempts"][0]["manager_outcome"] == "returned"
    assert "PRIVATE" not in json.dumps(report)
    assert launch_history_cli.main(["a" * 32, "--json", "--outcome", "returned"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "invalid-query"


def test_latest_observation_uses_existing_attempt_event_index(store):
    with sqlite3.connect(store.path) as connection:
        connection.create_function("manager_outcome", 4, launch_attempts._manager_outcome)
        plan = connection.execute(
            "EXPLAIN QUERY PLAN " + launch_attempts._OBSERVED_METADATA + " LIMIT 1", (0,),
        ).fetchall()
    assert any("events_attempt" in str(row) for row in plan)


def test_latest_observation_retains_exact_sqlite_float_timestamp(store, monkeypatch):
    stamp = 1792947902.0776796
    monkeypatch.setattr(launch_attempts, "_timestamp", lambda: stamp)
    store.append("a" * 32, owner="manager", phase="launcher-returned", exit_code=0)
    assert store.entries()[0]["last_manager_event"]["observed_at"] == stamp
    assert store.detail("a" * 32)["events"][-1]["observed_at"] == stamp
