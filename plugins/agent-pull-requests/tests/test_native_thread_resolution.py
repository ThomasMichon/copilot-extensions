"""Selected-runtime negotiation and loss semantics without a live provider."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agent_pull_requests import __main__ as cli, native_author, native_thread_resolution

FP = "sha256:" + "a" * 64


def support() -> dict:
    return {
        "schema": "agent-worktrees.pr-author-capabilities", "version": 1,
        "repo": "Project/repository", "provider": "azure-devops",
        "operations": {"thread-resolve": True, "worker-policy-config": True},
    }


def result(status: str = "confirmed") -> dict:
    return {
        "schema": "agent-worktrees.pr-thread-resolve", "version": 1,
        "repo": "Project/repository", "number": 7, "thread": 9,
        "operation_id": "fix:9", "provider": "azure-devops", "actor": "native-principal",
        "expected_candidate": FP, "expected_thread": FP,
        "observed_candidate": {"fingerprint": FP},
        "observed_thread": {"id": 9, "status": "closed", "fingerprint": FP},
        "ok": status == "confirmed", "actions": {"resolution": {"status": status}},
        "reason": "resolved" if status == "confirmed" else "operation-consumed",
        "error": "" if status == "confirmed" else "Retain the original operation; do not replay",
        "future_field": {"retained": True},
    }


@pytest.fixture
def command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    path = tmp_path / "selected-owner"
    path.touch()
    return str(path)


def arguments(command: str, *extra: str) -> list[str]:
    return [
        "resolve-thread", "--repo", "Project/repository", "--number", "7", "--thread", "9",
        "--expected-candidate", FP, "--expected-thread", FP, "--operation-id", "fix:9",
        "--worktrees-command", command, "--json", *extra,
    ]


def stub(monkeypatch: pytest.MonkeyPatch, declaration: Any, value: Any,
         code: int | None = None) -> list[list[str]]:
    calls = []
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1] == "pr-author-capabilities":
            if isinstance(declaration, Exception):
                raise declaration
            return subprocess.CompletedProcess(argv, 0, json.dumps(declaration), "")
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(
            argv, code if code is not None else (0 if value["ok"] else 1), json.dumps(value), "",
        )
    monkeypatch.setattr(native_author, "run_command", run)
    monkeypatch.setattr(native_thread_resolution, "run_command", run)
    return calls


@pytest.mark.parametrize("status", ["confirmed", "not-attempted", "failed", "unknown"])
def test_actual_frontend_preserves_owner_result_and_additive_fields(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, status: str,
) -> None:
    value = result(status)
    if status == "not-attempted":
        value["provider"] = None
    calls = stub(monkeypatch, support(), value)
    assert cli.main(arguments(command)) == (0 if value["ok"] else 1)
    assert json.loads(capsys.readouterr().out) == value
    assert calls[1][0:2] == [command, "pr-thread-resolve"] and len(calls) == 2
    assert "--thread=9" in calls[1] and f"--expected-candidate={FP}" in calls[1]


@pytest.mark.parametrize("declaration", [
    {}, {**support(), "operations": {}}, {**support(), "version": True},
    {**support(), "operations": {"thread-resolve": "true"}},
    {**support(), "provider": "github"}, {**support(), "provider": "gitea"},
    OSError("selected runtime unavailable"),
])
def test_old_malformed_unsupported_or_rolled_back_owner_never_gets_a_write(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, declaration: Any,
) -> None:
    calls = stub(monkeypatch, declaration, result())
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["actions"]["resolution"]["status"] == "not-attempted"
    assert body["thread"] == 9 and body["operation_id"] == "fix:9" and len(calls) == 1


@pytest.mark.parametrize("damage", [
    {"schema": "old"}, {"version": True}, {"repo": "other/repository"}, {"number": True},
    {"thread": 10}, {"operation_id": "another"}, {"expected_candidate": "another"},
    {"expected_thread": "another"}, {"provider": "github"}, {"actions": []},
    {"ok": False}, {"actor": None}, {"observed_candidate": None}, {"observed_thread": None},
])
def test_malformed_post_dispatch_envelope_is_unknown_without_replay(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, damage: dict,
) -> None:
    calls = stub(monkeypatch, support(), {**result(), **damage})
    assert cli.main(arguments(command)) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["actions"]["resolution"]["status"] == "unknown"
    assert body["repo"] == "Project/repository" and body["thread"] == 9 and body["operation_id"] == "fix:9"
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [OSError("lost response"), subprocess.TimeoutExpired(["owner"], 90)])
def test_lost_or_late_owner_does_not_invent_success_or_repeat(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, failure: Exception,
) -> None:
    calls = stub(monkeypatch, support(), failure)
    assert cli.main(arguments(command)) == 1
    assert json.loads(capsys.readouterr().out)["actions"]["resolution"]["status"] == "unknown"
    assert len(calls) == 2


def test_explicit_worker_policy_is_forwarded_to_both_owners(
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
def test_invalid_selection_or_expectations_never_probe_or_dispatch(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, extra: list[str],
) -> None:
    monkeypatch.setattr(native_author, "run_command", lambda *a, **k: pytest.fail("probe"))
    if extra[0] in ("--all", "--pr", "tracked"):
        with pytest.raises(SystemExit) as caught:
            cli.main(arguments(command, *extra))
        assert caught.value.code == 2
    else:
        assert cli.main(arguments(command, *extra)) == 1
        assert json.loads(capsys.readouterr().out)["actions"]["resolution"]["status"] == "not-attempted"
