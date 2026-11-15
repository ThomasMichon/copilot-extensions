"""Reporter naming crosses real readers without changing row or routing authority."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pytest

from worktree_manager import engine_client as ec
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, derive
from worktree_manager.production_picker.picker_tui.worktree_action_context import (
    build_worktree_action_context,
)
from worktree_manager.worktree_feed_contract import CurrentFeed
from worktree_manager.worktree_rows_contract import (
    CurrentSnapshot, parse_current_rows, publication_wire_rows,
)


def reporter(name: str = "current", environment: str = "wsl") -> dict:
    return {
        "version": 1, "status": "resolved", "hostname": "observed-host",
        "machine": name, "environment": environment, "execution_machine": f"{name}-guest",
    }


def envelope(*, version: int = 2, revision: int = 1) -> dict:
    return {
        "ok": True, "contract_version": version, "project": "project",
        "projection": {
            "schema_version": 1, "quality": "current", "generation": "a" * 32,
            "revision": revision, "observed_at": revision, "roster_count": 1,
            "selected_count": 1,
        },
        "worktrees": [{
            "id": "historical-owner-aaaa", "machine_identity_version": 1,
            "machine": "old-host", "execution_machine": "old-guest", "platform": "wsl",
            "environment": "wsl", "semantic_revision": 1,
        }],
        **({"observations": [{"id": "historical-owner-aaaa", "facts": {}}]}
           if version == 2 else {}),
    }


def frames(value: dict, *, initial: bool = True, semantic: bool = True) -> list[dict]:
    head = value["projection"]
    result = [{
        **{key: child for key, child in value.items() if key not in {
            "ok", "worktrees", "observations",
        }},
        "type": "begin" if initial else "update", "cursor": head["revision"],
        "base_cursor": None if initial else head["revision"] - 1, "selected_count": 1,
    }]
    if semantic:
        result.append({"type": "worktree", "projection": head, "wt": value["worktrees"][0]})
    for observation in value.get("observations", []):
        result.append({"type": "observation", "projection": head, "observation": observation})
    if not initial:
        result.append({"type": "order", "projection": head, "ids": [value["worktrees"][0]["id"]]})
    result.append({
        "type": "done" if initial else "commit", "projection": head, "cursor": head["revision"],
    })
    return result


def receive(receiver: CurrentFeed | CurrentSnapshot, events: list[dict]) -> list[dict]:
    rows = None
    for event in events:
        rows = receiver.receive(event)
    assert rows is not None
    return rows


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("present", [True, False])
def test_point_reader_prefers_remote_reporter_and_preserves_selected_route(
    monkeypatch: pytest.MonkeyPatch, local: bool, environment: str, present: bool,
) -> None:
    value = envelope()
    if present:
        value["reporting_context"] = reporter(environment=environment)
    before = deepcopy(value)
    payload = json.dumps(value)
    calls = []

    def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, payload, "")

    if local:
        monkeypatch.setattr(ec, "engine_base_command", lambda: ["synthetic-engine"])
        monkeypatch.setattr(data_local.context, "project", lambda: "project")
        rows = data_local.load("selected-route", "WSL", runner=runner)
    else:
        source = data_ssh.Source("selected-route", "WSL", ["synthetic-engine"], alias="peer")
        rows = data_ssh._fetch(source, runner=runner)
    assert len(calls) == 1
    row = rows[0]
    owner = "current-guest" if present else "old-guest"
    assert row["machine"] == "selected-route"
    assert row["source_id"] == data_ssh.machine_source_id("selected-route", "WSL")
    assert row["machine_identity"]["execution_machine"] == owner
    assert ec.worktree_from_dict(row["raw"]).machine == owner
    action = build_worktree_action_context(row)
    assert action["machine"] == "selected-route"
    assert action["execution_machine"] == owner
    assert action["physical_machine"] == ("current" if present else "old-host")
    assert action["environment"] == (environment if present else "wsl")
    assert publication_wire_rows([row["raw"]]) == value["worktrees"]
    assert value == before


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("receiver_type", [CurrentFeed, CurrentSnapshot])
def test_snapshot_context_is_admitted_outside_wire_rows(
    version: int, receiver_type: type,
) -> None:
    value = envelope(version=version)
    value["reporting_context"] = reporter()
    rows = receive(receiver_type(), frames(value))
    assert rows[0]["_reporting_context"] == value["reporting_context"]
    assert publication_wire_rows(rows) == value["worktrees"]
    assert rows == parse_current_rows(value)


def test_live_unavailable_never_revives_stamped_names_or_invalid_legacy_tuples() -> None:
    value = envelope()
    value["reporting_context"] = {"version": 1, "status": "unavailable"}
    value["worktrees"][0]["machine_identity_version"] = "obsolete"
    rows = parse_current_rows(value)
    assert ec.worktree_from_dict(rows[0]).machine == ""
    normalized = derive.norm(rows[0], "selected-route", "WSL")
    assert "machine_identity" not in normalized
    action = build_worktree_action_context(normalized)
    assert action["machine"] == "selected-route"
    assert not {"physical_machine", "execution_machine", "environment", "platform"} & action.keys()
    assert build_worktree_action_context({"raw": rows[0]})["machine"] == ""
    value["reporting_context"] = reporter()
    assert ec.worktree_from_dict(parse_current_rows(value)[0]).machine == "current-guest"
    del value["reporting_context"]
    with pytest.raises(ValueError, match="machine identity"):
        parse_current_rows(value)


@pytest.mark.parametrize("version", [1, 2])
def test_heartbeat_rename_withdrawal_and_availability_keep_revisions_and_route(version: int) -> None:
    value = envelope(version=version)
    value["reporting_context"] = reporter("first")
    receiver = CurrentFeed()
    receive(receiver, frames(value))
    before_head = deepcopy(receiver.head)
    before_wire = publication_wire_rows(receiver.rows)
    for context in (reporter("renamed"), {"version": 1, "status": "unavailable"}):
        changed = receiver.receive({
            "type": "heartbeat", "generation": "a" * 32, "cursor": 1,
            "reporting_context": context,
        })
        assert changed is not None
        assert changed[0]["_reporting_context"] == context
        assert receiver.head == before_head
        assert publication_wire_rows(changed) == before_wire
        assert receiver.receive({
            "type": "heartbeat", "generation": "a" * 32, "cursor": 1,
            "reporting_context": context,
        }) is None
    rows = receiver.receive({
        "type": "availability", "projection": before_head,
        "availability": {"status": "stale", "reason": "refresh failed"},
    })
    assert rows[0]["_reporting_context"]["status"] == "unavailable"
    assert rows[0]["_availability"]["status"] == "stale"
    assert ec.worktree_from_dict(rows[0]).machine == ""
    assert publication_wire_rows(rows) == before_wire


def test_context_updates_commit_atomically_and_provider_rollback_keeps_legacy_baseline() -> None:
    receiver = CurrentFeed()
    first = envelope()
    first["reporting_context"] = reporter("first")
    receive(receiver, frames(first))
    before = deepcopy(receiver.rows)
    second = envelope(revision=2)
    second["reporting_context"] = reporter("second")
    events = frames(second, initial=False, semantic=False)
    for event in events[:-1]:
        assert receiver.receive(event) is None
        assert receiver.rows == before
    rows = receiver.receive(events[-1])
    assert ec.worktree_from_dict(rows[0]).machine == "second-guest"
    assert rows[0]["semantic_revision"] == 1
    third = envelope(revision=3)
    rows = receive(receiver, frames(third, initial=False, semantic=False))
    assert "_reporting_context" not in rows[0]
    assert ec.worktree_from_dict(rows[0]).machine == "old-guest"


@pytest.mark.parametrize("bad", [
    None, [], {}, {"version": True, "status": "unavailable"},
    {"version": 2, "status": "unavailable"},
    {"version": 1, "status": "unavailable", "machine": "stale"},
    {**reporter(), "environment": []}, {**reporter(), "environment": "future"},
    {**reporter(), "machine": ""}, {**reporter(), "hostname": "x\nbad"},
    {**reporter(), "execution_machine": "x" * 257},
])
def test_malformed_context_refuses_point_snapshot_and_heartbeat_without_replacing_rows(
    bad: object,
) -> None:
    value = envelope()
    value["reporting_context"] = bad
    with pytest.raises(ValueError, match="reporting"):
        parse_current_rows(value)
    with pytest.raises(ValueError, match="reporting"):
        CurrentSnapshot().receive(frames(value)[0])
    receiver = CurrentFeed()
    receive(receiver, frames(envelope()))
    before, head = deepcopy(receiver.rows), deepcopy(receiver.head)
    with pytest.raises(ValueError, match="reporting"):
        receiver.receive({
            "type": "heartbeat", "generation": "a" * 32, "cursor": 1,
            "reporting_context": bad,
        })
    assert receiver.rows == before and receiver.head == head


def test_wire_row_cannot_inject_receiver_context() -> None:
    value = envelope()
    value["worktrees"][0]["_reporting_context"] = reporter("forged")
    with pytest.raises(ValueError, match="receiver reporting"):
        parse_current_rows(value)
