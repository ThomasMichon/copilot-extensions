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


def test_git_repo_connector_indexes_remote_default_branch_not_working_tree(
    tmp_path: Path,
) -> None:
    """The connector indexes the fetched remote default branch (origin/HEAD),
    not the local working tree: local unpushed commits and dirty files are
    excluded, and the cursor is the origin tip. A later push is picked up after
    the connector re-fetches (freshness)."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "dev@example.test")
    _git(seed, "config", "user.name", "Dev User")
    (seed / "README.md").write_text("# Remote canonical\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "seed origin main")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")

    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "config", "user.email", "dev@example.test")
    _git(work, "config", "user.name", "Dev User")
    # Diverge locally: an unpushed local commit plus an uncommitted dirty file.
    (work / "local_only.py").write_text("print('unpushed')\n", encoding="utf-8")
    _git(work, "add", "local_only.py")
    _git(work, "commit", "-m", "local only, never pushed")
    (work / "dirty.py").write_text("print('dirty working tree')\n", encoding="utf-8")

    origin_main = _git(work, "rev-parse", "origin/main").strip()
    local_head = _git(work, "rev-parse", "HEAD").strip()
    assert origin_main != local_head  # the checkout has genuinely diverged

    connector = GitRepoConnector(repo_path=work)
    paths = {entry.path for entry in connector.discover()}
    assert "README.md" in paths  # from origin's default branch
    assert "local_only.py" not in paths  # local unpushed commit excluded
    assert "dirty.py" not in paths  # working-tree dirt excluded
    assert connector.current_commit() == origin_main  # cursor is the origin tip

    # Advance origin, then a re-fetching connector sees the new commit (freshness).
    (seed / "feature.py").write_text("print('shipped')\n", encoding="utf-8")
    _git(seed, "add", "feature.py")
    _git(seed, "commit", "-m", "ship feature to origin main")
    _git(seed, "push", "origin", "main")

    fresh = GitRepoConnector(repo_path=work)
    changed = {entry.path for entry in fresh.discover_changed(origin_main)}
    assert "feature.py" in changed
    new_tip = _git(work, "rev-parse", "origin/main").strip()
    assert fresh.current_commit() == new_tip
    assert new_tip != origin_main


def test_git_repo_connector_falls_back_to_local_head_without_remote(tmp_path: Path) -> None:
    """A purely local repo (no remote) indexes the local HEAD/working tree."""
    repo = tmp_path / "local-only"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.test")
    _git(repo, "config", "user.name", "Dev User")
    (repo / "only.py").write_text("print('local')\n", encoding="utf-8")
    _git(repo, "add", "only.py")
    _git(repo, "commit", "-m", "local commit")
    head = _git(repo, "rev-parse", "HEAD").strip()

    connector = GitRepoConnector(repo_path=repo)
    paths = {entry.path for entry in connector.discover()}
    assert "only.py" in paths
    assert connector.current_commit() == head


def test_source_name_from_url_parsing() -> None:
    """Repo-name extraction handles https, scp-like, trailing slash and .git."""
    f = GitRepoConnector._repo_name_from_url
    assert f("https://github.com/tmichon_microsoft/dotfiles.git") == "dotfiles"
    assert f("https://github.com/tmichon_microsoft/dotfiles") == "dotfiles"
    assert f("git@github.com:owner/dotfiles.git") == "dotfiles"
    assert f("https://host/owner/repo/") == "repo"
    assert f(r"C:\repos\owner\dotfiles.git") == "dotfiles"
    assert f("") is None


def test_source_name_is_stable_across_worktrees(tmp_path: Path) -> None:
    """Two differently-named checkouts of the SAME remote repo share one source
    name (``git:<remote-repo>``), not ``git:<checkout-folder>`` -- so indexing
    from a linked worktree updates the canonical source instead of minting a
    spurious per-worktree one (#1350)."""
    origin = tmp_path / "canonical.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(origin), str(seed))
    _git(seed, "config", "user.email", "dev@example.test")
    _git(seed, "config", "user.name", "Dev User")
    (seed / "README.md").write_text("# Canonical\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "-u", "origin", "main")

    # Two checkouts with different folder basenames, same origin.
    anchor = tmp_path / "dotfiles"
    worktree = tmp_path / "tmichon-cloud1-win-20260812-xyz"
    _git(tmp_path, "clone", str(origin), str(anchor))
    _git(tmp_path, "clone", str(origin), str(worktree))

    assert GitRepoConnector(repo_path=anchor).source_name == "git:canonical"
    assert GitRepoConnector(repo_path=worktree).source_name == "git:canonical"
