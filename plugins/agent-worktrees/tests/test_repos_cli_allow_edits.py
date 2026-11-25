"""CLI duration parsing preserves the grant owner's normalization contract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import repos, repos_cli


@pytest.mark.parametrize("minutes,expected", [
    ("bogus", 10), ("", 10), ("5", 5), ("999", 60), ("nan", 10),
])
def test_allow_edits_cli_preserves_duration_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], minutes: str, expected: int,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(repos, "find_repo", lambda _name: None)
    assert repos_cli.cmd_repos_dispatch([
        "allow-edits", "example", "--reason", "synthetic CLI duration fixture",
        "--minutes", minutes, "--json",
    ]) == 0
    assert json.loads(capfd.readouterr().out)["minutes"] == expected
    stored = json.loads((tmp_path / ".agent-worktrees" / "allow-edits.json").read_text())
    assert stored["grants"]["example"]["minutes"] == expected
