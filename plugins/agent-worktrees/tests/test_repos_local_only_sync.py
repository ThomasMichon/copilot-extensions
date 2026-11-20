"""Registry-sync classification through local Git and actual CLI consumers."""

from __future__ import annotations

from pathlib import Path
import subprocess
from unittest.mock import Mock

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import git_ops, repos, repos_cli


def _git(path: Path, *args: str) -> str:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True,
        text=True, **no_window_kwargs(),
    )
    return result.stdout.strip()


def _repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "tracked.txt").write_text("unchanged\n", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-m", "initial")


def _entry(path: Path) -> repos.RepoEntry:
    return repos.RepoEntry(
        name=path.name, repo_class="singleton", default_branch="main",
        paths={"windows": str(path)},
    )


@pytest.mark.parametrize("current", ["main", "topic"])
def test_local_only_sync_preserves_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current: str,
) -> None:
    work = tmp_path / "local-only"
    _repository(work)
    if current != "main":
        _git(work, "checkout", "-b", current)
    paths = [
        work / ".git" / name
        for name in ("config", "HEAD", "index")
    ]
    paths.extend([work / ".git" / "logs" / "HEAD", work / "tracked.txt"])
    before = {path: path.read_bytes() for path in paths}
    fetch = Mock(side_effect=AssertionError("local-only sync must not fetch"))
    monkeypatch.setattr(git_ops, "fetch", fetch)

    assert repos.sync_repo(_entry(work), plat="windows") == (
        "skipped", "local-only repository (no remotes configured)",
    )

    fetch.assert_not_called()
    assert _git(work, "branch", "--show-current") == current
    assert _git(work, "remote") == ""
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_remote_observation_failure_is_not_local_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    work = tmp_path / "unavailable"
    (work / ".git").mkdir(parents=True)
    calls: list[tuple[str, ...]] = []

    def observe(path: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        assert path == str(work)
        calls.append(args)
        if args == ("remote",):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(["git", "remote"], timeout)
            return subprocess.CompletedProcess(args, 2, "", "remote observation failed")
        answers: dict[tuple[str, ...], str] = {
            ("rev-parse", "--is-bare-repository"): "false",
            ("status", "--porcelain"): "",
            ("branch", "--show-current"): "main",
        }
        assert args in answers
        return subprocess.CompletedProcess(args, 0, answers[args], "")

    fetch = Mock(side_effect=AssertionError("failed observation must not fetch"))
    monkeypatch.setattr(repos, "_git", observe)
    monkeypatch.setattr(git_ops, "fetch", fetch)

    state, detail = repos.sync_repo(_entry(work), plat="windows")

    assert state == "error"
    assert "remote" in detail
    assert calls[-1] == ("remote",)
    fetch.assert_not_called()


def test_non_origin_remote_is_not_local_only(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    work = tmp_path / "work"
    _repository(upstream)
    _repository(work)
    _git(work, "remote", "add", "upstream", str(upstream))

    state, detail = repos.sync_repo(_entry(work), plat="windows")

    assert state == "error"
    assert detail
    assert _git(work, "remote") == "upstream"


@pytest.mark.parametrize("detail", [
    "authentication failed", "transport unavailable", "fetch timed out",
])
def test_configured_remote_fetch_errors_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: str,
) -> None:
    work = tmp_path / "configured"
    _repository(work)
    _git(work, "remote", "add", "origin", str(tmp_path / "upstream"))
    fetch = Mock(side_effect=git_ops.GitError(["git", "fetch"], 128, detail))
    monkeypatch.setattr(git_ops, "fetch", fetch)

    assert repos.sync_repo(_entry(work), plat="windows") == ("error", detail)

    fetch.assert_called_once_with(
        "origin", cwd=str(work), timeout=180, isolated_repository=True,
    )


def test_registry_and_cli_keep_local_skip_and_real_errors_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(repos, "_current_platform", lambda: "windows")
    local = tmp_path / "a-local"
    upstream = tmp_path / "upstream"
    remote = tmp_path / "b-remote"
    broken = tmp_path / "c-broken"
    for path in (local, upstream, broken):
        _repository(path)
    _git(tmp_path, "clone", str(upstream), str(remote))
    _git(broken, "remote", "add", "origin", str(tmp_path / "absent"))
    for path in (local, remote, broken):
        repos.add_repo(
            path.name, str(path), repo_class="singleton",
            default_branch="main", plat="windows",
        )
    registry = repos._repos_yaml_path()
    before = registry.read_bytes()

    results = {
        name: (state, detail)
        for name, state, detail in repos.sync_all(plat="windows")
    }

    assert results["a-local"] == (
        "skipped", "local-only repository (no remotes configured)",
    )
    assert results["b-remote"] == ("synced", "main")
    assert results["c-broken"][0] == "error"
    capsys.readouterr()
    assert repos_cli.cmd_repos_dispatch(["sync"]) == 1
    output = capsys.readouterr().out
    assert "a-local: skipped (local-only repository (no remotes configured))" in output
    assert "b-remote: main" in output
    assert "c-broken:" in output
    assert repos_cli.cmd_repos_dispatch(["sync", "a-local", "b-remote"]) == 0
    assert "c-broken:" not in capsys.readouterr().out
    assert _git(local, "remote") == ""
    assert registry.read_bytes() == before
