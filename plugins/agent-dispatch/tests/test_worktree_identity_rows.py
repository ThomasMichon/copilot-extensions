"""Exercise the actual raw-JSON reader and focus projection, not a substituted reader."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch import identity


def _tuple_row(environment="wsl", owner="custom-guest"):
    return {
        "id": "same-worktree", "machine_identity_version": 1,
        "machine": "physical-host", "environment": environment,
        "execution_machine": owner, "platform": environment,
        "summary": "bounded work", "status_note_at": "2026-10-20T12:00:00",
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

    def supply(rows, *, envelope=True):
        payload = {"worktrees": rows} if envelope else rows

        def read(*args, timeout):
            calls.append((args, timeout))
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

        monkeypatch.setattr(identity, "run_agent_worktrees_capture", read)
        return calls

    return supply


@pytest.mark.parametrize("envelope", [True, False])
def test_raw_identity_rows_filter_by_execution_owner_not_shared_physical_host(
    raw_records, envelope,
):
    native = _tuple_row("windows", "custom-native")
    guest = _tuple_row()
    legacy = {"machine": "old-owner", "id": "old-worktree", "summary": "legacy work"}
    rows = [native, guest, legacy]
    before = deepcopy(rows)
    calls = raw_records(rows, envelope=envelope)
    assert identity.aw_list_records(machine="custom-guest") == [guest]
    assert identity.aw_list_records(machine="custom-native") == [native]
    assert identity.aw_list_records(machine="physical-host") == []
    assert identity.aw_list_records(machine="old-owner") == [legacy]
    assert identity.aw_list_records() == rows
    assert rows == before
    assert calls == [(("list", "--json"), 20)] * 5


@pytest.mark.parametrize("listing", [True, False])
def test_focus_tuple_uses_execution_owner_and_preserves_full_identity(
    raw_records, monkeypatch, capsys, listing,
):
    guest = _tuple_row()
    raw_records([_tuple_row("windows", "custom-native"), guest])
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("custom-guest", "same-worktree"))
    command = ["focus", "--list", "--machine", "custom-guest"] if listing else ["focus"]
    assert cli.main(command) == 0
    output = capsys.readouterr()
    row = json.loads(output.out)[0] if listing else json.loads(output.out)
    assert row == {
        "machine": "custom-guest", "worktree": "same-worktree",
        "focus": "bounded work", "updated_at": "2026-10-20T12:00:00",
        "machine_identity": {
            key: guest[key] for key in (
                "machine_identity_version", "machine", "environment",
                "execution_machine", "platform",
            )
        },
    }
    assert output.err == ""


def test_focus_legacy_output_shape_is_unchanged(raw_records, capsys):
    raw_records([{
        "machine": "legacy-owner", "id": "old-worktree", "summary": " legacy work ",
        "status_note_at": "historical", "environment": "ignored-without-version",
    }])
    assert cli.main(["focus", "--list"]) == 0
    assert json.loads(capsys.readouterr().out) == [{
        "machine": "legacy-owner", "worktree": "old-worktree",
        "focus": "legacy work", "updated_at": "historical",
    }]


@pytest.mark.parametrize("field,value", [
    ("machine_identity_version", True), ("machine_identity_version", 2),
    ("machine", ""), ("environment", "win"), ("environment", "macos"),
    ("execution_machine", None), ("execution_machine", " guest"),
    ("platform", "windows"),
])
@pytest.mark.parametrize("selector", [None, "custom-native", "unrelated-owner"])
def test_malformed_identity_cannot_be_hidden_by_filtering(raw_records, field, value, selector):
    malformed = _tuple_row()
    malformed[field] = value
    malformed["summary"] = ""
    raw_records([_tuple_row("windows", "custom-native"), malformed])
    with pytest.raises(ValueError, match="machine identity"):
        identity.aw_list_records(machine=selector)


@pytest.mark.parametrize("listing", [True, False])
def test_focus_invalid_identity_reports_failure_without_partial_or_empty_success(
    raw_records, monkeypatch, capsys, listing,
):
    malformed = _tuple_row()
    del malformed["execution_machine"]
    raw_records([_tuple_row("windows", "custom-native"), malformed])
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("custom-guest", "same-worktree"))
    command = ["focus", "--list", "--machine", "unrelated"] if listing else ["focus"]
    assert cli.main(command) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "agent-dispatch:" in output.err
    assert "machine identity execution_machine" in output.err
