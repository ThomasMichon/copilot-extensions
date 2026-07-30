from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent_index.sources.git_repo import GitRepoConnector

_GIT = shutil.which("git") or "git"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        [_GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def test_git_repo_connector_discovers_files_commits_and_changes(tmp_path: Path) -> None:
    repo = tmp_path / "sample-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "dev@example.test")
    _git(repo, "config", "user.name", "Dev User")
    (repo / "README.md").write_text("# Hello\n", encoding="utf-8")
    (repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _git(repo, "add", "README.md", "image.png")
    _git(repo, "commit", "-m", "Initial commit")
    first = _git(repo, "rev-parse", "HEAD").strip()

    connector = GitRepoConnector(repo_path=repo)
    entries = connector.discover()
    paths = {entry.path for entry in entries}
    assert "README.md" in paths
    assert "image.png" not in paths
    assert any(path.startswith("commits/") for path in paths)
    assert connector.current_commit() == first

    listed = connector.list_paths()
    assert listed[f"git:{repo.name}"] == {"README.md"}
    assert f"commits/{first}.txt" in listed[f"git:{repo.name}:commits"]

    (repo / "src.py").write_text("print('hi')\n", encoding="utf-8")
    _git(repo, "add", "src.py")
    _git(repo, "commit", "-m", "Add source")
    changed = connector.discover_changed(first)
    changed_paths = {entry.path for entry in changed}
    assert "src.py" in changed_paths
    assert "README.md" not in changed_paths
    assert any(entry.source == f"git:{repo.name}:commits" for entry in changed)
