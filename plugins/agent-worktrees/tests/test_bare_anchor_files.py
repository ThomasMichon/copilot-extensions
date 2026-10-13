"""A bare anchor's materialized files follow its branch ref on ``repos sync``.

Real git throughout: an upstream repo, and a bare anchor made the way these
anchors exist in practice (an ordinary clone flipped to ``core.bare=true``, so
it keeps its index and checked-out files beside the git dir).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import bare_anchor_files, bare_anchor_sync, repos


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _anchor_git(anchor: Path, *args: str) -> str:
    return _git(anchor, "-c", "core.bare=false", "--work-tree", str(anchor), *args)


def _commit(repo: Path, message: str, files: dict[str, str], *, force: bool = False) -> str:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", *(["-f"] if force else []), *files)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit(repo, "init", {"README.md": "v1\n", ".gitignore": "*.log\n"})
    return repo


def _make_anchor(upstream: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", str(upstream), str(dest)], check=True, capture_output=True)
    _git(dest, "config", "user.email", "t@example.com")
    _git(dest, "config", "user.name", "Test")
    _git(dest, "config", "core.bare", "true")
    return dest


def _ref(anchor: Path) -> str:
    return _git(anchor, "rev-parse", "refs/heads/main")


def test_refreshes_on_ff(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    new = _commit(upstream, "second", {"README.md": "v2\n", "docs/NEW.md": "new\n"})

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    # Files first: a ref-only sync moves the ref but leaves these stale.
    assert _ref(anchor) == new
    assert _read(anchor / "README.md") == "v2\n"
    assert _read(anchor / "docs" / "NEW.md") == "new\n"
    assert _anchor_git(anchor, "status", "--porcelain") == ""
    assert (state, detail) == ("synced", "main; files=refreshed")


def test_up_to_date_reports_current_files(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")

    assert bare_anchor_sync.sync_bare_anchor(str(anchor), "main") == (
        "synced", "main; files=current",
    )


def test_dirty_blocks_ref_and_files(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    _commit(upstream, "second", {"README.md": "v2\n", "NEW.md": "new\n"})
    (anchor / "README.md").write_text("local edit\n", encoding="utf-8")

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert state == "skipped"
    assert detail.endswith("files=dirty")
    assert _ref(anchor) == old
    assert _read(anchor / "README.md") == "local edit\n"
    assert not (anchor / "NEW.md").exists()


def test_ignored_collision_refuses(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    # Upstream starts tracking a path the anchor holds as an IGNORED local file.
    _commit(upstream, "track a log", {"build.log": "upstream\n"}, force=True)
    (anchor / "build.log").write_text("local ignored\n", encoding="utf-8")

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert state == "skipped"
    assert detail.endswith("files=collision")
    assert _ref(anchor) == old
    assert _read(anchor / "build.log") == "local ignored\n"


def test_ignored_file_at_parent_of_new_path_refuses(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    # Upstream tracks `cache.log/entry.txt`; the anchor holds an IGNORED local
    # FILE `cache.log` that git would delete to make room for the directory.
    _commit(upstream, "track under a log dir", {"cache.log/entry.txt": "upstream\n"}, force=True)
    (anchor / "cache.log").write_text("local ignored\n", encoding="utf-8")

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert state == "skipped"
    assert detail.endswith("files=collision")
    assert _ref(anchor) == old
    assert _read(anchor / "cache.log") == "local ignored\n"


def test_ignored_file_inside_directory_at_new_path_refuses(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    _commit(upstream, "track a log file", {"out.log": "upstream\n"}, force=True)
    (anchor / "out.log").mkdir()
    (anchor / "out.log" / "keep.txt").write_text("local\n", encoding="utf-8")

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert (state, _ref(anchor)) == ("skipped", old)
    assert detail.endswith("files=collision")
    assert _read(anchor / "out.log" / "keep.txt") == "local\n"


def test_tracked_directory_becoming_a_file_refreshes(upstream: Path, tmp_path: Path) -> None:
    _commit(upstream, "add a dir", {"conf/a.txt": "a\n", "conf/b.txt": "b\n"})
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    _git(upstream, "rm", "-q", "-r", "conf")
    new = _commit(upstream, "dir to file", {"conf": "now a file\n"})

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert (state, detail) == ("synced", "main; files=refreshed")
    assert _ref(anchor) == new
    assert _read(anchor / "conf") == "now a file\n"


def test_case_only_rename_refreshes(upstream: Path, tmp_path: Path) -> None:
    _commit(upstream, "add notes", {"notes.md": "n\n"})
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    _git(upstream, "mv", "notes.md", "NOTES.md")
    _git(upstream, "commit", "-m", "case rename")
    new = _git(upstream, "rev-parse", "HEAD")

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert (state, detail) == ("synced", "main; files=refreshed")
    assert _ref(anchor) == new
    assert "NOTES.md" in {p.name for p in anchor.iterdir()}


def _lock_index_after_precheck(monkeypatch, anchor: Path) -> Path:
    """Simulate another git process taking the index lock between the
    (real) pre-checks and the refresh."""
    lock = anchor / ".git" / "index.lock"
    real = bare_anchor_files.precheck

    def precheck_then_lock(path: str, old: str, new: str):
        result = real(path, old, new)
        if Path(path).resolve() == anchor.resolve():
            lock.write_text("", encoding="utf-8")
        return result

    monkeypatch.setattr(bare_anchor_files, "precheck", precheck_then_lock)
    return lock


def test_held_index_lock_refuses_before_ref_moves(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    _commit(upstream, "second", {"README.md": "v2\n"})
    lock = anchor / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")
    finally:
        lock.unlink()

    assert (state, _ref(anchor)) == ("skipped", old)
    assert detail.endswith("files=index-locked")
    assert _read(anchor / "README.md") == "v1\n"


def test_refresh_failure_leaves_ref_moved_and_reports(
    upstream: Path, tmp_path: Path, monkeypatch
) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    new = _commit(upstream, "second", {"README.md": "v2\n"})
    lock = _lock_index_after_precheck(monkeypatch, anchor)
    try:
        state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")
    finally:
        lock.unlink(missing_ok=True)

    assert state == "synced"
    assert f"advanced from {old[:12]}" in detail
    assert bare_anchor_files.status_of(detail) == "refresh-failed"
    assert _ref(anchor) == new
    assert _read(anchor / "README.md") == "v1\n"


def test_stale_index(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    middle = _commit(upstream, "second", {"README.md": "v2\n"})
    _commit(upstream, "third", {"README.md": "v3\n"})
    # A ref-only advance (as earlier syncs did) leaves the index behind HEAD.
    _git(anchor, "fetch", "--quiet", "origin")
    _git(anchor, "update-ref", "refs/heads/main", middle)

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert state == "skipped"
    assert detail.endswith("files=stale-index")
    assert _ref(anchor) == middle
    assert _read(anchor / "README.md") == "v1\n"


def test_pure_bare_none(upstream: Path, tmp_path: Path) -> None:
    # A bare anchor that never had a work tree: `<anchor>/.git` is a plain bare
    # clone, with no index and no materialized files.
    anchor = tmp_path / "pure"
    subprocess.run(
        ["git", "clone", "--bare", str(upstream), str(anchor / ".git")],
        check=True, capture_output=True,
    )
    new = _commit(upstream, "second", {"README.md": "v2\n"})

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert (state, detail) == ("synced", "main; files=none")
    assert _git(anchor, "rev-parse", "refs/heads/main") == new
    assert not (anchor / ".git" / "index").exists()
    assert sorted(p.name for p in anchor.iterdir()) == [".git"]


def test_linked_worktree_untouched(upstream: Path, tmp_path: Path) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    linked = tmp_path / "linked"
    _git(anchor, "worktree", "add", "-b", "feature", str(linked), "main")
    (linked / "README.md").write_text("linked edit\n", encoding="utf-8")
    (linked / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    linked_head = _git(linked, "rev-parse", "HEAD")
    linked_status = _git(linked, "status", "--porcelain")
    _commit(upstream, "second", {"README.md": "v2\n"})

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert (state, detail) == ("synced", "main; files=refreshed")
    assert _read(anchor / "README.md") == "v2\n"
    assert _git(linked, "rev-parse", "HEAD") == linked_head
    assert _git(linked, "status", "--porcelain") == linked_status
    assert _read(linked / "README.md") == "linked edit\n"
    assert _read(linked / "scratch.txt") == "untracked\n"


def test_status_of_extracts_files_token() -> None:
    assert bare_anchor_files.status_of("main; files=refreshed") == "refreshed"
    assert bare_anchor_files.status_of("x; files=refresh-failed:fatal: boom") == "refresh-failed"
    assert bare_anchor_files.status_of("x; files=refresh-failed:bad files=dirty") == "refresh-failed"
    assert bare_anchor_files.status_of("main") is None


def test_repos_cli_sync_warns_on_blocked_anchor_files(
    home: Path, upstream: Path, tmp_path: Path, monkeypatch
) -> None:
    from agent_worktrees import output, repos_cli

    dirty = _make_anchor(upstream, tmp_path / "dirty-anchor")
    clean = _make_anchor(upstream, tmp_path / "clean-anchor")
    locked = _make_anchor(upstream, tmp_path / "locked-anchor")
    _commit(upstream, "second", {"README.md": "v2\n"})
    (dirty / "README.md").write_text("local edit\n", encoding="utf-8")
    repos.add_repo("dirty-anchor", str(dirty), repo_class="worktree", default_branch="main")
    repos.add_repo("clean-anchor", str(clean), repo_class="worktree", default_branch="main")
    repos.add_repo("locked-anchor", str(locked), repo_class="worktree", default_branch="main")

    calls: list[tuple[str, str]] = []
    for level in ("ok", "info", "warn", "err"):
        monkeypatch.setattr(output, level, lambda msg, _l=level: calls.append((_l, msg)))

    lock = _lock_index_after_precheck(monkeypatch, locked)
    try:
        rc = repos_cli.cmd_repos_dispatch(["sync", "dirty-anchor", "clean-anchor", "locked-anchor"])
    finally:
        lock.unlink(missing_ok=True)

    assert rc == 0
    by_name = {msg.split(":", 1)[0]: level for level, msg in calls}
    assert by_name["dirty-anchor"] == "warn", calls
    assert by_name["clean-anchor"] == "ok", calls
    assert by_name["locked-anchor"] == "warn", calls
    assert any("files=dirty" in msg for _, msg in calls)
    assert any("files=refresh-failed:" in msg for _, msg in calls)


# --------------------------------------------------------------------------
# Inherited Git selectors never redirect the sync to another repository
# --------------------------------------------------------------------------


def _decoy(tmp_path: Path) -> Path:
    """An unrelated ordinary repo that ambient GIT_* selectors point at."""
    repo = tmp_path / "decoy"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit(repo, "decoy", {"DECOY.md": "decoy\n"})
    _git(repo, "remote", "add", "origin", str(tmp_path / "upstream"))
    return repo


def _decoy_state(decoy: Path) -> tuple:
    git_dir = decoy / ".git"
    return (
        _git(decoy, "for-each-ref", "--format=%(refname) %(objectname)"),
        (git_dir / "index").read_bytes(),
        _git(decoy, "config", "--get-all", "remote.origin.fetch"),
        sorted(p.name for p in decoy.iterdir()),
        _read(decoy / "DECOY.md"),
    )


def _point_git_at(monkeypatch, decoy: Path) -> None:
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))


def test_sync_ignores_inherited_git_selectors(
    upstream: Path, tmp_path: Path, monkeypatch
) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    decoy = _decoy(tmp_path)
    before = _decoy_state(decoy)
    new = _commit(upstream, "second", {"README.md": "v2\n", "docs/NEW.md": "new\n"})

    with monkeypatch.context() as m:
        _point_git_at(m, decoy)
        result = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")

    assert result == ("synced", "main; files=refreshed")
    assert _ref(anchor) == new
    assert _read(anchor / "docs" / "NEW.md") == "new\n"
    assert _anchor_git(anchor, "status", "--porcelain") == ""
    assert _decoy_state(decoy) == before


@pytest.mark.parametrize("step", ["refresh", "precheck", "current_status"])
def test_anchor_files_ignore_inherited_git_selectors(
    upstream: Path, tmp_path: Path, monkeypatch, step: str
) -> None:
    anchor = _make_anchor(upstream, tmp_path / "anchor")
    decoy = _decoy(tmp_path)
    old = _ref(anchor)
    new = _commit(upstream, "second", {"README.md": "v2\n"})
    _git(anchor, "fetch", "--quiet", "origin")
    if step == "precheck":
        (anchor / "README.md").write_text("local edit\n", encoding="utf-8")
    if step == "refresh":
        _git(anchor, "update-ref", "refs/heads/main", new, old)
    before = _decoy_state(decoy)

    with monkeypatch.context() as m:
        _point_git_at(m, decoy)
        if step == "refresh":
            result = bare_anchor_files.refresh(str(anchor), old, new)
        elif step == "precheck":
            result = bare_anchor_files.precheck(str(anchor), old, new)
        else:
            result = bare_anchor_files.current_status(str(anchor), old)

    expected = {"refresh": "refreshed", "precheck": "dirty", "current_status": "current"}
    assert result == expected[step]
    if step == "refresh":
        assert _read(anchor / "README.md") == "v2\n"
    assert _decoy_state(decoy) == before


def test_documented_recovery_command_works_from_any_directory(
    upstream: Path, tmp_path: Path, monkeypatch
) -> None:
    """The `refresh-failed` recovery in the CLI reference must name the
    anchor's git dir, so it works without first changing into the anchor."""
    doc = Path(__file__).resolve().parents[1] / "docs" / "cli-reference.md"
    row = next(ln for ln in doc.read_text(encoding="utf-8").splitlines()
               if ln.startswith("| `refresh-failed:"))
    command = re.search(r"then run `(git [^`]+)`", row).group(1)

    anchor = _make_anchor(upstream, tmp_path / "anchor")
    old = _ref(anchor)
    lock = _lock_index_after_precheck(monkeypatch, anchor)
    _commit(upstream, "second", {"README.md": "v2\n"})
    try:
        state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), "main")
    finally:
        lock.unlink(missing_ok=True)
    assert bare_anchor_files.status_of(detail) == "refresh-failed"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    argv = [
        part.replace("<anchor>", anchor.as_posix())
        .replace("<from>", old[:12]).replace("<branch>", "main")
        for part in shlex.split(command)
    ]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    ran = subprocess.run(argv, cwd=elsewhere, env=env, capture_output=True, text=True)

    assert ran.returncode == 0, ran.stderr
    assert _read(anchor / "README.md") == "v2\n"
    assert _anchor_git(anchor, "status", "--porcelain") == ""


def test_branch_switch_hint_works_from_any_directory(upstream: Path, tmp_path: Path) -> None:
    """The suggested `symbolic-ref` command names the anchor and is quoted for
    the platform shell, so it works from any directory even when the path and
    branch contain shell metacharacters."""
    anchor = _make_anchor(upstream, tmp_path / "an$HOME'`chor")
    branch = "rel$HOME`x"
    _git(anchor, "branch", branch)

    state, detail = bare_anchor_sync.sync_bare_anchor(str(anchor), branch)

    assert state == "skipped"
    command = detail.split("switch it explicitly with: ", 1)[1]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    shell = (["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
             if os.name == "nt" else ["sh", "-c", command])
    ran = subprocess.run(shell, cwd=elsewhere, env=env, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert _git(anchor, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
