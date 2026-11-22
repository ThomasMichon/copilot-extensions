"""Foreign fork selection crosses only an explicitly capable native owner."""

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agent_pull_requests import __main__ as cli, native_author, native_cli


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("supported", [False, True])
def test_native_foreign_fork_requires_capability_before_forwarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    workspace: bool, supported: bool,
) -> None:
    command = tmp_path / "worktrees"
    command.touch()
    reads: list[list[str]] = []
    effects: list[list[str]] = []

    def capabilities(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        reads.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "schema": "agent-worktrees.pr-author-capabilities", "version": 1,
            "repo": "upstream/target", "provider": "github",
            "operations": {"qualified-create": True, "foreign-source-repo": supported},
        }), "")

    def create(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        effects.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "repo": "upstream/target", "number": 7, "pr_opened": True,
            "url": "https://github.com/upstream/target/pull/7",
        }), "")

    monkeypatch.setattr(native_author, "run_command", capabilities)
    monkeypatch.setattr(native_cli, "run_command", create)
    monkeypatch.setattr(cli, "_github_create", lambda *a, **k: pytest.fail("forge fallback"))
    args = [
        "create", "--repo", "upstream/target", "--head", "contributor:topic",
        "--source-repo", "contributor/renamed-source", "--title", "Fix",
        "--worktrees-command", str(command), "--json",
    ]
    if workspace:
        args += ["--source-workspace", str(tmp_path), "--expected-head", "a" * 40]
    assert cli.main(args) == (0 if supported else 1)
    payload = json.loads(capsys.readouterr().out)
    assert len(reads) == 1
    assert len(effects) == (1 if supported else 0)
    if supported:
        assert "--from-branch=contributor:topic" in effects[0]
        assert "--source-repo=contributor/renamed-source" in effects[0]
        assert ("--expected-head=" + "a" * 40 in effects[0]) is workspace
        assert payload["number"] == 7
    else:
        assert "does not advertise" in payload["error"]


def test_foreign_source_without_native_owner_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_github_create", lambda *a, **k: pytest.fail("forge fallback"))
    assert cli.main([
        "create", "--repo", "upstream/target", "--head", "contributor:topic",
        "--source-repo", "contributor/renamed-source", "--title", "Fix", "--json",
    ]) == 2
    assert "explicit --worktrees-command" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("supported", [False, True])
def test_fork_discovery_negotiates_one_creation_deadline_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    supported: bool,
) -> None:
    import time

    command = tmp_path / "worktrees"
    command.touch()
    reads: list[list[str]] = []
    effects: list[dict[str, Any]] = []
    before = time.monotonic()

    def capabilities(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        reads.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "schema": "agent-worktrees.pr-author-capabilities", "version": 1,
            "repo": "upstream/target", "provider": "github",
            "operations": {"foreign-source-repo": True, "foreign-create-deadline-v1": supported},
        }), "")

    def create(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        effects.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "repo": "upstream/target", "number": 7, "pr_opened": True,
            "url": "https://github.com/upstream/target/pull/7",
        }), "")

    monkeypatch.setattr(native_author, "run_command", capabilities)
    monkeypatch.setattr(native_cli, "run_command", create)
    assert cli.main([
        "create", "--repo", "upstream/target", "--head", "contributor:topic", "--title", "Fix",
        "--worktrees-command", str(command), "--json",
    ]) == (0 if supported else 1)
    payload = json.loads(capsys.readouterr().out)
    assert len(reads) == 1
    if supported:
        assert len(effects) == 1
        deadline = float(effects[0]["env"]["AGENT_WORKTREES_FOREIGN_CREATE_DEADLINE_V1"])
        assert before < deadline <= time.monotonic() + 75
        assert effects[0]["timeout"] == 90
    else:
        assert not effects and "--source-repo" in payload["error"]


def test_native_creation_deadline_crosses_a_real_child_without_replacing_its_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import sys
    import time
    from agent_pull_requests.native_command import run_command

    monkeypatch.setenv("NATIVE_CREATION_FIXTURE", "inherited")
    key = "AGENT_WORKTREES_FOREIGN_CREATE_DEADLINE_V1"
    deadline = str(time.monotonic() + 75)
    result = run_command([
        sys.executable, "-c",
        "import json,os; print(json.dumps({key:os.environ[key] for key in "
        "['NATIVE_CREATION_FIXTURE','AGENT_WORKTREES_FOREIGN_CREATE_DEADLINE_V1','PATH']}))",
    ], timeout=15, env={key: deadline})
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == {
        "NATIVE_CREATION_FIXTURE": "inherited", key: deadline, "PATH": os.environ["PATH"],
    }
