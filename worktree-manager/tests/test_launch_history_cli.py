"""Discoverable read-only history never replays an action or leaks private input."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from worktree_manager import __main__ as cli, engine_client, launch_history_cli as history
from worktree_manager.launch_attempts import LaunchAttempts


@pytest.fixture
def saved(tmp_path, monkeypatch):
    store = LaunchAttempts(tmp_path)
    monkeypatch.setattr(history, "LaunchAttempts", lambda: store)
    attempt = uuid4().hex
    store.record_intent(
        attempt, action="new", project="example", surface="new-dialog",
        machine="selected-host", environment="linux", is_local=False,
        prompt="  private fixture\n exact input ", options={"no_mux": True},
    )
    return store, attempt


def evidence(attempt, **changes):
    return {
        "schema": history._SCHEMA, "version": 1, "attempt_id": attempt,
        "scope": "local-project", "receipts": [], "admissions": [], "errors": [],
        "has_more": False, "next_after": None, "prompt_acceptance": "unknown",
        **changes,
    }


def test_main_routes_discoverable_list_without_starting_engine(saved, monkeypatch, capsys):
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: pytest.fail("engine list"))
    assert cli.main(["launch-attempts", "--json", "--machine", "selected-host"]) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["scope"] == "local-manager"
    assert report["attempts"][0]["attempt_id"] == saved[1]
    assert "private fixture" not in raw and "input" not in report["attempts"][0]
    assert cli.main(["--help"]) == 0
    assert "launch-attempts" in capsys.readouterr().out


def test_detail_calls_versioned_local_engine_once_without_prompt(saved, monkeypatch, capsys):
    store, attempt = saved
    request = uuid4().hex
    store.append(attempt, owner="manager", phase="resolved", request_id=request)
    calls = []
    def run(project, args, **kwargs):
        calls.append((project, args, kwargs))
        return evidence(attempt)
    monkeypatch.setattr(engine_client, "run_json", run)
    assert history.main([attempt, "--json"]) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert "private fixture" not in raw
    assert report["attempt"]["machine"] == "selected-host"
    assert report["engine"]["scope"] == "local-project"
    assert report["prompt_acceptance"] == "unknown"
    assert calls == [("example", [
        "launch-attempts", attempt, "--json", "--limit", "100", "--request-id", request,
    ], {})]
    assert [event["phase"] for event in store.detail(attempt)["events"]] == ["confirmed", "resolved"]


def test_input_requires_explicit_detail_and_preserves_exact_text(saved, monkeypatch, capsys):
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: evidence(saved[1]))
    assert history.main(["--include-input", "--json"]) == 2
    capsys.readouterr()
    assert history.main([saved[1], "--include-input", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["attempt"]["input"]["prompt"] == (
        "  private fixture\n exact input "
    )


@pytest.mark.parametrize("error", [
    engine_client.EngineError("private command or stderr"),
    engine_client.EngineError("missing", install_hint=True),
])
def test_unavailable_engine_preserves_local_intent_and_sanitized_source_error(saved, monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(engine_client, "run_json", fail)
    assert history.main([saved[1], "--json"]) == 1
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["attempt"]["attempt_id"] == saved[1]
    assert report["engine"] == {
        "scope": "local-project", "status": "unavailable", "code": "unavailable-or-incompatible",
    }
    assert "private command" not in raw and "private fixture" not in raw


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 2}, {"attempt_id": "foreign"},
    {"scope": "selected-target"}, {"prompt_acceptance": "accepted"},
    {"receipts": {}}, {"has_more": 1}, {"input": "private unexpected response"},
])
def test_mismatched_or_incompatible_receipts_do_not_claim_evidence(saved, monkeypatch, capsys, changes):
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: evidence(saved[1], **changes))
    assert history.main([saved[1], "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["engine"]["status"] == "unavailable"


def test_filters_use_journal_query_and_do_not_materialize_input(saved, monkeypatch, capsys):
    store, attempt = saved
    assert store.entries(machine="selected-host", environment="linux")[0]["attempt_id"] == attempt
    assert store.entries(machine="not-selected") == []
    assert store.entries(after=0, before=1) == []
    assert store.entries(provider="unknown", venue_id="unknown") == []
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: pytest.fail("list engine"))
    assert history.main(["--json", "--after", "0", "--environment", "linux"]) == 0
    assert len(json.loads(capsys.readouterr().out)["attempts"]) == 1


def test_pagination_flags_reach_existing_event_and_receipt_owners(saved, monkeypatch, capsys):
    store, attempt = saved
    store.append(attempt, owner="manager", phase="resolution-requested")
    calls = []
    def run(project, args, **kwargs):
        calls.append(args)
        return evidence(attempt, has_more=True, next_after="b" * 32 + ".json")
    monkeypatch.setattr(engine_client, "run_json", run)
    assert history.main([
        attempt, "--json", "--event-limit", "1", "--after-sequence", "1",
        "--receipt-limit", "2", "--receipt-after", "a" * 32 + ".json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [event["phase"] for event in report["attempt"]["events"]] == ["resolution-requested"]
    assert calls[0][-2:] == ["--after", "a" * 32 + ".json"]
    assert report["engine"]["has_more"]


def test_list_cursor_preserves_equal_timestamp_rows(saved, monkeypatch, capsys):
    from worktree_manager import launch_attempts
    store, _ = saved
    monkeypatch.setattr(launch_attempts, "_timestamp", lambda: 1234)
    for i in range(3):
        store.record_intent(
            str(i) * 32, action="open", project="example", surface="picker", is_local=True,
        )
    assert history.main(["--json", "--action", "open", "--limit", "2"]) == 0
    page = json.loads(capsys.readouterr().out)
    assert [row["attempt_id"] for row in page["attempts"]] == ["2" * 32, "1" * 32]
    assert page["has_more"]
    assert history.main([
        "--json", "--action", "open", "--limit", "2", "--before", str(page["next_before"]),
        "--before-id", page["next_before_id"],
    ]) == 0
    page = json.loads(capsys.readouterr().out)
    assert [row["attempt_id"] for row in page["attempts"]] == ["0" * 32]
    assert not page["has_more"]


def test_json_errors_remain_structured_and_do_not_create_missing_storage(tmp_path, monkeypatch, capsys):
    store = LaunchAttempts(tmp_path)
    monkeypatch.setattr(history, "LaunchAttempts", lambda: store)
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: pytest.fail("no intent"))
    assert history.main([uuid4().hex, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"] == [
        {"source": "manager", "code": "unreadable-or-expired"},
    ]
    assert history.main(["--include-input", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "invalid-query"
    assert list(tmp_path.iterdir()) == []
