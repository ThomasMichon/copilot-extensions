"""Conditional read-only binding consumer; ordinary binding has no new gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/binding-knowledge/scripts/bind_knowledge.py"
spec = importlib.util.spec_from_file_location("binding_preflight_consumer", SCRIPT)
assert spec is not None and spec.loader is not None
binding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binding)


def complete(value: Any, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, json.dumps(value), "private-provider-diagnostic")


def observation(mode: str = "unknown") -> dict[str, Any]:
    return {
        "contract": "agent-worktrees.repository-registration-preflight", "version": 1,
        "status": "partial" if mode == "unknown" else "observed",
        "registration": {"name": "knowledge", "source": "canonical"},
        "landing": {"status": "unknown" if mode == "unknown" else "observed", "mode": mode},
    }


@pytest.mark.parametrize("capabilities", [None, {}, {"repository_registration_preflight": True},
                                         {"repository_registration_preflight": 2}])
def test_missing_or_unsupported_owner_never_receives_new_syntax(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], capabilities: object,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, args))
        return complete({"repos": [], "capabilities": capabilities})

    monkeypatch.setattr(binding, "_run_agent_worktrees", run)
    monkeypatch.setattr(binding, "bind", lambda *args, **kwargs: pytest.fail("unexpected binding"))
    assert binding.main(["--preflight", "--knowledge", "knowledge", "--agent-worktrees-path", "selected-owner"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "unsupported"
    assert value["landing"]["mode"] == "unknown"
    assert calls == [("selected-owner", ["repos", "list", "--json"])]


@pytest.mark.parametrize("mode", ["direct", "pull-request", "unknown"])
def test_capable_owner_is_consumed_without_auth_or_binding_writes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    expected = observation(mode)

    def run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, args))
        if len(calls) == 1:
            return complete({"capabilities": {"repository_registration_preflight": 1}, "repos": []})
        return complete(expected)

    monkeypatch.setattr(binding, "_run_agent_worktrees", run)
    monkeypatch.setattr(binding, "bind", lambda *args, **kwargs: pytest.fail("unexpected binding"))
    assert binding.main(["--preflight", "--knowledge", "knowledge", "--agent-worktrees-path", "selected-owner"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert calls[1] == ("selected-owner", [
        "repos", "registration", "preflight", "--repository", "knowledge",
    ])


@pytest.mark.parametrize("failure", ["rollback", "malformed", "wrong-target", "wrong-version", "null"])
def test_native_failure_is_not_replayed_or_printed_as_policy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    calls: list[list[str]] = []
    expected = observation()
    if failure == "wrong-target":
        expected["registration"]["name"] = "another-repository"
    elif failure == "wrong-version":
        expected["version"] = True

    def run(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) == 1:
            return complete({"capabilities": {"repository_registration_preflight": 1}})
        if failure == "rollback":
            return complete({"private": "credential-shaped-output"}, 2)
        if failure == "malformed":
            return subprocess.CompletedProcess([], 0, "private-output", "private-error")
        return complete(None if failure == "null" else expected)

    monkeypatch.setattr(binding, "_run_agent_worktrees", run)
    assert binding.main(["--preflight", "--knowledge", "knowledge", "--agent-worktrees-path", "selected-owner"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "failed"
    assert "private" not in output.out + output.err
    assert len(calls) == 2


@pytest.mark.parametrize("extra", [
    ["--register"], ["--configuration", "apply"], ["--harness", "harness"],
    ["--account", "selected-user"], ["--knowledge-path", "selected-path"],
    ["--capabilities"],
])
def test_read_mode_refuses_effectful_or_irrelevant_options(
    monkeypatch: pytest.MonkeyPatch, extra: list[str],
) -> None:
    monkeypatch.setattr(binding, "_run_agent_worktrees", lambda *args, **kwargs: pytest.fail("unexpected read"))
    with pytest.raises(SystemExit) as caught:
        binding.main([
            "--preflight", "--knowledge", "knowledge", "--agent-worktrees-path", "selected-owner", *extra,
        ])
    assert caught.value.code == 2
