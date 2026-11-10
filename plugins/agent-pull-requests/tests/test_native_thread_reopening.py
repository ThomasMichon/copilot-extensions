"""Optional reopening negotiation preserves close-only and uncertain outcomes."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agent_pull_requests import __main__ as cli, native_author
from test_native_thread_resolution import FP, command, stub
from test_native_thread_resolution import arguments as close_arguments
from test_native_thread_resolution import result as close_result
from test_native_thread_resolution import support as close_support

__all__ = ["command"]


def support() -> dict:
    return {**close_support(), "operations": {"thread-resolve": True, "thread-reopen": True,
                                            "worker-policy-config": True}}


def result(status: str = "confirmed") -> dict:
    value = close_result(status)
    value["schema"] = "agent-worktrees.pr-thread-reopen"
    value["actions"] = {"reopening": value["actions"]["resolution"]}
    value["observed_thread"]["status"] = "active"
    value["reason"] = "reopened" if status == "confirmed" else value["reason"]
    return value


def arguments(command: str, *extra: str) -> list[str]:
    return ["reopen-thread", *close_arguments(command, *extra)[1:]]


@pytest.mark.parametrize("status", ["confirmed", "not-attempted", "failed", "unknown"])
def test_actual_frontend_preserves_selected_owner_envelope(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, status: str,
) -> None:
    value = result(status)
    if status == "not-attempted":
        value["provider"] = None
    calls = stub(monkeypatch, support(), value)
    assert cli.main(arguments(command)) == (0 if value["ok"] else 1)
    assert json.loads(capsys.readouterr().out) == value
    assert len(calls) == 2 and calls[1][:2] == [command, "pr-thread-reopen"]
    assert "--thread=9" in calls[1] and f"--expected-candidate={FP}" in calls[1]


@pytest.mark.parametrize("declaration", [
    {}, close_support(), {**support(), "operations": {}}, {**support(), "version": True},
    {**support(), "operations": {"thread-reopen": "true"}},
    {**support(), "operations": {"thread-reopen": False}},
    {**support(), "operations": {"thread-reopen": 1}},
    {**support(), "provider": "github"}, {**support(), "provider": "gitea"},
    OSError("selected runtime unavailable"), subprocess.TimeoutExpired(["owner"], 20),
])
def test_old_malformed_unsupported_and_rollback_support_never_dispatch(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, declaration: Any,
) -> None:
    calls = stub(monkeypatch, declaration, result())
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["schema"] == "agent-worktrees.pr-thread-reopen"
    assert body["actions"]["reopening"]["status"] == "not-attempted" and len(calls) == 1


@pytest.mark.parametrize("damage", [
    {"schema": "agent-worktrees.pr-thread-resolve"}, {"version": True}, {"repo": "other/repository"},
    {"number": True}, {"thread": 10}, {"operation_id": "another"}, {"expected_candidate": "another"},
    {"expected_thread": "another"}, {"provider": "github"}, {"actions": []}, {"ok": False},
    {"actor": None}, {"observed_candidate": None}, {"observed_thread": None},
    {"actions": {"resolution": {"status": "confirmed"}}},
    {"observed_thread": {"id": 9, "status": True, "fingerprint": FP}},
    {"observed_thread": {"id": 9, "status": 1.0, "fingerprint": FP}},
    {"observed_thread": {"id": 9, "status": "closed", "fingerprint": FP}},
])
def test_unconfirmed_postdispatch_output_is_unknown_not_replayed(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, damage: dict,
) -> None:
    calls = stub(monkeypatch, support(), {**result(), **damage})
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["actions"]["reopening"]["status"] == "unknown"
    assert body["thread"] == 9 and body["operation_id"] == "fix:9" and len(calls) == 2


@pytest.mark.parametrize("failure", [OSError("lost response"), subprocess.TimeoutExpired(["owner"], 90)])
def test_lost_owner_output_keeps_unknown_identity(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, failure: Exception,
) -> None:
    calls = stub(monkeypatch, support(), failure)
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["actions"]["reopening"]["status"] == "unknown" and len(calls) == 2


def test_numeric_active_confirmation_is_valid(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    value = result()
    value["observed_thread"]["status"] = 1
    calls = stub(monkeypatch, support(), value)
    assert cli.main(arguments(command)) == 0
    assert json.loads(capsys.readouterr().out) == value and len(calls) == 2


def test_delivered_policy_is_forwarded_to_both_owners(
    command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{}", encoding="utf-8")
    calls = stub(monkeypatch, support(), result("unknown"))
    assert cli.main(arguments(command, "--config", str(path))) == 1
    assert json.loads(capsys.readouterr().out)["future_field"] == {"retained": True}
    assert all(f"--config={path}" in call for call in calls)


@pytest.mark.parametrize("extra", [
    ["--all"], ["--pr", "7"], ["tracked"], ["--thread", "0"],
    ["--operation-id", "invalid key"], ["--expected-candidate", "head-only"],
])
def test_invalid_selection_never_probes_or_dispatches(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, extra: list[str],
) -> None:
    monkeypatch.setattr(native_author, "run_command", lambda *a, **k: pytest.fail("probe"))
    if extra[0] in ("--all", "--pr", "tracked"):
        with pytest.raises(SystemExit) as caught:
            cli.main(arguments(command, *extra))
        assert caught.value.code == 2
    else:
        assert cli.main(arguments(command, *extra)) == 1
        assert json.loads(capsys.readouterr().out)["actions"]["reopening"]["status"] == "not-attempted"
