"""Exercise the actual raw-JSON reader and focus projection, not a substituted reader."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch import identity


def _tuple_row():
    return {
        "id": "same-worktree", "machine_identity_version": 1,
        "machine": "old-physical-host", "environment": "windows",
        "execution_machine": "old-native", "platform": "windows",
        "summary": "bounded work", "status_note_at": "2026-10-20T12:00:00",
    }


def _reporter(environment="wsl", owner="current-guest"):
    return {
        "version": 1, "status": "resolved", "hostname": "observed-host",
        "machine": "current-physical-host", "environment": environment,
        "execution_machine": owner,
    }


@pytest.fixture
def raw_records(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.delenv("AGENT_DISPATCH_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_FAILOVER_MACHINE", raising=False)
    monkeypatch.setattr(
        cli, "_client", lambda *_a, **_k: pytest.fail("focus must not contact a coordinator"),
    )
    calls = []

    def supply(rows, *, envelope=True, reporter=..., legacy_machine="current-guest"):
        payload = {"worktrees": rows} if envelope else rows
        if reporter is not ...:
            payload["reporting_context"] = reporter

        def read(*args, timeout):
            calls.append((args, timeout))
            if args == ("get", "machine"):
                return subprocess.CompletedProcess(args, 0, legacy_machine or "", "")
            assert args == ("list", "--json")
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

        monkeypatch.setattr(identity, "run_agent_worktrees_capture", read)
        return calls

    return supply


@pytest.mark.parametrize("environment,owner", [
    ("windows", "current-native"), ("wsl", "current-guest"), ("linux", "current-linux"),
])
def test_raw_rows_use_reporting_host_not_persisted_identity(
    raw_records, environment, owner,
):
    stamped = _tuple_row()
    legacy = {"machine": "old-owner", "id": "old-worktree", "summary": "legacy work"}
    unstamped = {"id": "plain-worktree", "summary": "new work"}
    rows = [stamped, legacy, unstamped]
    before = deepcopy(rows)
    calls = raw_records(rows, reporter=_reporter(environment, owner))
    expected_names = {
        "machine_identity_version": 1, "machine": "current-physical-host",
        "environment": environment, "execution_machine": owner, "platform": environment,
    }
    expected = [{**row, **expected_names} for row in rows]
    assert identity.aw_list_records(machine=owner) == expected
    assert identity.aw_list_records(machine="old-owner") == []
    assert identity.aw_list_records(machine="current-physical-host") == (
        expected if owner == "current-physical-host" else []
    )
    assert identity.aw_list_records() == expected
    assert rows == before
    assert calls == [(("list", "--json"), 20)] * 4


def test_reporter_rename_changes_next_read_without_rewriting_rows(raw_records):
    rows = [_tuple_row()]
    before = deepcopy(rows)
    reporter = _reporter()
    raw_records(rows, reporter=reporter)
    assert identity.aw_list_records(machine="current-guest")[0]["id"] == "same-worktree"
    reporter.update(machine="renamed-host", execution_machine="renamed-guest")
    assert identity.aw_list_records(machine="current-guest") == []
    assert identity.aw_list_records(machine="renamed-guest")[0]["machine"] == "renamed-host"
    assert rows == before


@pytest.mark.parametrize("listing", [True, False])
def test_focus_uses_reporter_identity_for_naming_free_and_stamped_rows(
    raw_records, monkeypatch, capsys, listing,
):
    row = _tuple_row() if listing else {
        "id": "same-worktree", "summary": "bounded work",
        "status_note_at": "2026-10-20T12:00:00",
    }
    raw_records([row], reporter=_reporter())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("current-guest", "same-worktree"))
    command = ["focus", "--list", "--machine", "current-guest"] if listing else ["focus"]
    assert cli.main(command) == 0
    output = capsys.readouterr()
    row = json.loads(output.out)[0] if listing else json.loads(output.out)
    assert row == {
        "machine": "current-guest", "worktree": "same-worktree",
        "focus": "bounded work", "updated_at": "2026-10-20T12:00:00",
        "machine_identity": {
            "machine_identity_version": 1, "machine": "current-physical-host",
            "environment": "wsl", "execution_machine": "current-guest", "platform": "wsl",
        },
    }
    assert output.err == ""


@pytest.mark.parametrize("envelope", [True, False])
def test_legacy_provider_uses_live_machine_probe_not_row_labels(raw_records, capsys, envelope):
    rows = [_tuple_row(), {
        "machine": "legacy-owner", "id": "old-worktree", "summary": " legacy work ",
        "status_note_at": "historical", "environment": "ignored-without-version",
    }]
    calls = raw_records(rows, envelope=envelope, legacy_machine="renamed-guest")
    assert cli.main(["focus", "--list", "--machine", "renamed-guest"]) == 0
    assert json.loads(capsys.readouterr().out) == [{
        "machine": "renamed-guest", "worktree": "same-worktree",
        "focus": "bounded work", "updated_at": "2026-10-20T12:00:00",
    }, {
        "machine": "renamed-guest", "worktree": "old-worktree",
        "focus": "legacy work", "updated_at": "historical",
    }]
    assert calls == [(("list", "--json"), 20), (("get", "machine"), 15)]


@pytest.mark.parametrize("field,value", [
    ("machine_identity_version", True), ("machine_identity_version", 2),
    ("machine", ""), ("environment", "win"), ("environment", "macos"),
    ("execution_machine", None), ("execution_machine", " guest"),
    ("platform", "windows"),
])
def test_legacy_tuple_fields_are_ignored_even_when_malformed(raw_records, field, value):
    malformed = _tuple_row()
    malformed[field] = value
    raw_records([malformed], reporter=_reporter())
    assert identity.aw_list_records(machine="current-guest")[0]["execution_machine"] == "current-guest"


@pytest.mark.parametrize("listing", [True, False])
@pytest.mark.parametrize("reporter", [
    None, [],
    {"version": 1, "status": "unavailable"},
    {**_reporter(), "version": True},
    {**_reporter(), "version": 2},
    {**_reporter(), "execution_machine": ""},
    {**_reporter(), "environment": "macos"},
    {**_reporter(), "hostname": None},
    {**_reporter(), "hostname": "\ud800"},
])
def test_focus_invalid_reporting_context_does_not_fall_back_to_stamps_or_local_probe(
    raw_records, monkeypatch, capsys, listing, reporter,
):
    calls = raw_records([_tuple_row()], reporter=reporter)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("current-guest", "same-worktree"))
    command = ["focus", "--list", "--machine", "unrelated"] if listing else ["focus"]
    assert cli.main(command) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "agent-dispatch:" in output.err
    assert "reporting" in output.err
    assert calls == [(("list", "--json"), 20)]


def test_legacy_provider_without_live_machine_does_not_inherit_stamps(raw_records, capsys):
    raw_records([_tuple_row()], legacy_machine=None)
    assert cli.main(["focus", "--list"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "reporting" in output.err
