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
