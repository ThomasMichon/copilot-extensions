"""Unit tests for session detritus detection (venvs, git clones, node_modules)."""

from __future__ import annotations

from pathlib import Path

from agent_logger.sync import detritus


def _make_session_files(root: Path) -> Path:
    files = root / "session-state" / "abc-123" / "files"
    files.mkdir(parents=True)
    return files.parent  # the session dir


def test_discovers_python_venv_root(tmp_path: Path) -> None:
    session = _make_session_files(tmp_path)
    venv = session / "files" / "agent-dispatch-install-probe"
    site_packages = venv / "Lib" / "site-packages" / "pip" / "_vendor" / "certifi"
    site_packages.mkdir(parents=True)
    (site_packages / "cacert.pem").write_bytes(b"-----BEGIN CERTIFICATE-----")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (venv / "Scripts").mkdir()
    (venv / "Scripts" / "python.exe").write_bytes(b"stub")

    summary = detritus.discover_session_tree_detritus(session)

    assert summary.roots == (Path("files/agent-dispatch-install-probe"),)
    assert summary.file_count == 3
    assert summary.measurement_complete is True


def test_discovers_git_clone_root(tmp_path: Path) -> None:
    session = _make_session_files(tmp_path)
    clone = session / "files" / "harness-clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (clone / "README.md").write_text("hello\n", encoding="utf-8")

    summary = detritus.discover_session_tree_detritus(session)

    assert summary.roots == (Path("files/harness-clone"),)
    assert summary.file_count == 2


def test_discovers_git_worktree_gitlink_file(tmp_path: Path) -> None:
    session = _make_session_files(tmp_path)
    clone = session / "files" / "linked-worktree"
    clone.mkdir(parents=True)
    (clone / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n", encoding="utf-8")
    (clone / "README.md").write_text("hello\n", encoding="utf-8")

    summary = detritus.discover_session_tree_detritus(session)

    assert summary.roots == (Path("files/linked-worktree"),)


def test_discovers_node_modules_by_name(tmp_path: Path) -> None:
    session = _make_session_files(tmp_path)
    modules = session / "files" / "tool" / "node_modules"
    pkg = modules / "left-pad"
    pkg.mkdir(parents=True)
    (pkg / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
    (modules / "package.json").write_text("{}", encoding="utf-8")

    summary = detritus.discover_session_tree_detritus(session)

    assert summary.roots == (Path("files/tool/node_modules"),)
    assert summary.file_count == 2


def test_does_not_flag_plain_directories(tmp_path: Path) -> None:
    session = _make_session_files(tmp_path)
    (session / "files" / "notes.md").write_text("hello\n", encoding="utf-8")
    plain = session / "files" / "some-folder" / "nested"
    plain.mkdir(parents=True)
    (plain / "data.json").write_text("{}", encoding="utf-8")

    summary = detritus.discover_session_tree_detritus(session)

    assert summary.roots == ()
    assert summary.file_count == 0


def test_measure_tree_is_best_effort_when_entries_limit_is_hit(
    tmp_path: Path, monkeypatch
) -> None:
    """A large already-detected root (e.g. node_modules) must not raise when
    it exceeds the bounded-scan limits -- exclusion still proceeds, only the
    reported size becomes a partial estimate."""
    monkeypatch.setattr(detritus, "MAX_DETRITUS_ENTRIES", 2)
    root = tmp_path / "node_modules"
    root.mkdir()
    for i in range(5):
        (root / f"file{i}.js").write_text("x", encoding="utf-8")

    files, nbytes, complete = detritus._measure_tree(root)

    assert complete is False
    assert files < 5
    assert nbytes >= 0


def test_measure_tree_is_best_effort_when_directories_limit_is_hit(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(detritus, "MAX_DETRITUS_DIRECTORIES", 1)
    root = tmp_path / "node_modules"
    (root / "pkg-a").mkdir(parents=True)
    (root / "pkg-b").mkdir(parents=True)

    _files, _nbytes, complete = detritus._measure_tree(root)

    assert complete is False


def test_discover_excludes_oversized_node_modules_without_raising(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: discovery must still succeed and exclude the root even
    when its measurement hits the bounded-scan limit. Uses a single
    top-level package (so the outer discovery walk's own, separate entry
    bound is never touched) whose nested files are numerous enough to trip
    only ``_measure_tree``'s bound."""
    monkeypatch.setattr(detritus, "MAX_DETRITUS_ENTRIES", 5)
    session = _make_session_files(tmp_path)
    pkg = session / "files" / "node_modules" / "pkg"
    pkg.mkdir(parents=True)
    for i in range(10):
        (pkg / f"file{i}.js").write_text("x", encoding="utf-8")

    summary = detritus.discover_session_tree_detritus(session)

    assert summary.roots == (Path("files/node_modules"),)
    assert summary.measurement_complete is False
