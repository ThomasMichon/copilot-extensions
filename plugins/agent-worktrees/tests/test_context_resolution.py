"""Tests for git-like CWD-based context resolution.

Covers the resolver that discovers the active project + worktree from the
current directory (or an explicit ``--project``), and the core anti-contamination
guarantee: ambient ``WORKTREE_PROJECT`` / ``WORKTREE_ID`` / ``WORKTREE_REPO`` are
never trusted for identity when the directory is authoritative.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NoReturn, TypedDict

import pytest
from machine_transport import detect_platform

from agent_worktrees import __main__ as m
from agent_worktrees import worktree_identity
from agent_worktrees import config as cfg
from agent_worktrees import git_ops
from agent_worktrees import installer as inst
from agent_worktrees import repos
from agent_worktrees import sessions, tracking
from conftest import _published_seed_environment, _seed_directory

ContextRepo = tuple[Path, Path, Path, str]
AdoptedRepo = tuple[Path, Path, Path, str, cfg.Config]


class StatusCapture(TypedDict, total=False):
    cwd: Path
    wt: str | None


# Captured at import time, before conftest.py's autouse
# `_assume_valid_claimant_worktree` fixture ever gets a chance to patch it --
# this is the real implementation the `adopted_repo` fixture below restores
# for every test in this file that needs genuine CWD-based resolution.
_REAL_INFER_WORKTREE_ID_FROM_CWD = worktree_identity._infer_worktree_id_from_cwd


# These legacy helpers are dynamically exposed by __main__. Resolve each
# callable at call time so the tests retain its current monkeypatch target.
def _resolve_active_project(project: str | None) -> tuple[str | None, Path | None]:
    resolve: Callable[[str | None], tuple[str | None, Path | None]] = m._resolve_active_project
    return resolve(project)


def _anchor_for_project(project: str) -> Path | None:
    anchor: Callable[[str], Path | None] = m._anchor_for_project
    return anchor(project)


def _safe_cwd() -> Path | None:
    cwd: Callable[[], Path | None] = m._safe_cwd
    return cwd()


def _git_toplevel(path: Path | None) -> Path | None:
    toplevel: Callable[[Path | None], Path | None] = m._git_toplevel
    return toplevel(path)


def _cwd_is_inside_project(anchor: Path) -> bool:
    inside: Callable[[Path], bool] = m._cwd_is_inside_project
    return inside(anchor)


def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


@pytest.fixture(scope="session")
def _context_repo_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    with _seed_directory(tmp_path_factory, "context-resolution") as (seed, build):
        if build:
            with _published_seed_environment(tmp_path_factory):
                anchor = seed / "myrepo"
                git_ops.git("init", "-b", "master", str(anchor))
                _git("config", "user.email", "t@example.com", cwd=anchor)
                _git("config", "user.name", "Test", cwd=anchor)
                (anchor / "f.txt").write_text("x\n")
                _git("add", "-A", cwd=anchor)
                _git("commit", "-m", "init", cwd=anchor)
                git_ops.git(
                    "worktree", "add",
                    str(seed / "myrepo.worktrees" / "myrepo-wt-001"),
                    "-b", "worktree/myrepo-wt-001", "master", cwd=str(anchor),
                )
        return seed


def _copy_context_repo(seed: Path, destination: Path) -> ContextRepo:
    shutil.copytree(
        seed, destination, dirs_exist_ok=True, copy_function=shutil.copyfile,
    )
    anchor = destination / "myrepo"
    wt_root = destination / "myrepo.worktrees"
    wt_id = "myrepo-wt-001"
    wt_path = wt_root / wt_id
    _git("worktree", "repair", str(wt_path), cwd=anchor)
    return anchor, wt_root, wt_path, wt_id


@pytest.fixture
def adopted_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _context_repo_seed: Path,
) -> AdoptedRepo:
    """Independent real Git stores, with fresh registration and tracking."""
    anchor, wt_root, wt_path, wt_id = _copy_context_repo(_context_repo_seed, tmp_path)

    monkeypatch.setattr(
        inst, "read_projects_registry",
        lambda: {"projects": {"myproj": {"anchor": str(anchor)}}},
    )
    monkeypatch.setattr(
        "agent_worktrees.repos.read_registry",
        lambda **_kwargs: repos.ReposRegistry(),
    )

    tdir = tmp_path / "tracking"
    tdir.mkdir()
    (tdir / f"{wt_id}.yaml").write_text("id: x\n")
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda name=None: tdir)

    conf = cfg.Config(
        srcroot=str(tmp_path), machine="t", platform="linux", repo_name="myproj",
        repos={"myproj": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(wt_root),
            default_branch="master", remote="origin",
        )},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: conf)

    # This file deliberately exercises REAL CWD-based git identity resolution
    # (copilot-extensions#59 and friends) -- override conftest.py's autouse
    # `_assume_valid_claimant_worktree` fixture, which defaults
    # `worktree_identity._infer_worktree_id_from_cwd` to a fixed fake id for
    # every other test suite-wide. Restoring the real implementation here
    # (not just undoing the patch) is what lets `_infer_worktree_id`'s own
    # git-dir-based resolution run for real against this fixture's repo.
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id_from_cwd",
        _REAL_INFER_WORKTREE_ID_FROM_CWD,
    )

    return anchor, wt_root, wt_path, wt_id, conf


@pytest.mark.parametrize("iteration", range(2))
def test_context_repo_copies_preserve_seed_and_sibling(
    adopted_repo: AdoptedRepo, _context_repo_seed: Path, tmp_path: Path,
    iteration: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, wt_root, first_wt, wt_id, conf = adopted_repo
    assert cfg.load_config() is conf
    assert conf.srcroot == str(tmp_path)
    assert conf.default_repo.anchor == str(first)
    assert conf.default_repo.worktree_root == str(wt_root)
    assert inst.read_projects_registry() == {
        "projects": {"myproj": {"anchor": str(first)}},
    }
    assert repos.read_registry().repos == {}
    tracking_file = cfg.tracking_dir() / f"{wt_id}.yaml"
    assert tracking_file.read_text() == "id: x\n"
    assert list(cfg.tracking_dir().iterdir()) == [tracking_file]
    assert Path(_git("rev-parse", "--show-toplevel", cwd=first_wt)).resolve() == first_wt.resolve()
    assert _git("branch", "--show-current", cwd=first_wt) == f"worktree/{wt_id}"
    seed_before = {
        path.relative_to(_context_repo_seed): path.read_bytes()
        for path in _context_repo_seed.rglob("*") if path.is_file()
    }
    sibling_root = tmp_path / "sibling"
    second, _, second_wt, _ = _copy_context_repo(_context_repo_seed, sibling_root)
    assert Path(_git("rev-parse", "--show-toplevel", cwd=second_wt)).resolve() == second_wt.resolve()
    sibling_before = {
        path.relative_to(sibling_root): path.read_bytes()
        for path in sibling_root.rglob("*") if path.is_file()
    }
    monkeypatch.chdir(first_wt)
    assert _resolve_active_project(None) == ("myproj", None)
    assert worktree_identity._infer_worktree_id(None, conf) == wt_id
    _git("branch", "only-first", cwd=first)
    _git("config", "core.bare", "true", cwd=first)
    (first_wt / "f.txt").write_text(f"changed {iteration}\n")
    tracking_file.write_text(f"id: private-{iteration}\n")
    assert _git("branch", "--list", "only-first", cwd=second) == ""
    assert _git("rev-parse", "--is-bare-repository", cwd=second) == "false"
    assert (second_wt / "f.txt").read_text() == "x\n"
    assert seed_before == {
        path.relative_to(_context_repo_seed): path.read_bytes()
        for path in _context_repo_seed.rglob("*") if path.is_file()
    }
    assert sibling_before == {
        path.relative_to(sibling_root): path.read_bytes()
        for path in sibling_root.rglob("*") if path.is_file()
    }


# ---------------------------------------------------------------------------
# Reverse lookup + project resolution
# ---------------------------------------------------------------------------

def test_reverse_lookup_from_anchor(adopted_repo: AdoptedRepo) -> None:
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    assert m._reverse_lookup_project(anchor) == "myproj"


def test_reverse_lookup_via_home_relative_repos_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#4190: a repos-registry entry stored home-relative (``~/repo``) must still
    reverse-lookup to its project. The WSL test-chamber anchor is registered as
    ``~/src/test-chamber``; before the ``RepoEntry.local_path`` expanduser fix,
    ``_reverse_lookup_project``'s repos fallback normalized the literal ``~``
    onto CWD and never matched, so CWD->project discovery failed for that repo.

    This drives the *repos-registry fallback* specifically: the projects
    registry is empty, so the only path to a match is the repos entry -- exactly
    the path that was broken."""
    fake_home = tmp_path / "home"
    (fake_home / "src" / "test-chamber").mkdir(parents=True)
    # expanduser reads these at call time (posix: HOME; nt: USERPROFILE).
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    plat = detect_platform()
    entry = repos.RepoEntry(
        name="test-chamber", paths={plat: "~/src/test-chamber"})
    monkeypatch.setattr(
        inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(
        "agent_worktrees.repos.read_registry",
        lambda: repos.ReposRegistry(repos={"test-chamber": entry}))

    anchor = fake_home / "src" / "test-chamber"
    assert m._reverse_lookup_project(anchor) == "test-chamber"


def test_anchor_for_project_via_home_relative_repos_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#4190 companion: ``_anchor_for_project`` (which realizes ``--project X``
    and backs the reverse lookup) must resolve a home-relative repos entry to
    the real absolute anchor dir -- its ``.is_dir()`` gate fails on a literal
    ``~`` path, so before the fix ``--project`` fell back to a broken path."""
    fake_home = tmp_path / "home"
    (fake_home / "src" / "test-chamber").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    plat = detect_platform()
    entry = repos.RepoEntry(
        name="test-chamber", paths={plat: "~/src/test-chamber"})
    monkeypatch.setattr(
        inst, "read_projects_registry", lambda: {"projects": {}})
    monkeypatch.setattr(
        "agent_worktrees.repos.read_registry",
        lambda: repos.ReposRegistry(repos={"test-chamber": entry}))

    resolved = _anchor_for_project("test-chamber")
    assert resolved is not None
    assert resolved == (fake_home / "src" / "test-chamber").resolve()


def test_resolve_from_anchor_cwd(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(anchor)
    project, assumed = _resolve_active_project(None)
    assert project == "myproj"
    assert assumed is None  # assumed CWD stays the real CWD


def test_resolve_from_bare_anchor_cwd(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``core.bare=true`` anchor (agent-worktrees' own pattern once worktrees
    are attached) must still resolve from its own directory.

    ``git rev-parse --show-toplevel`` always fails in a bare repo ("this
    operation must be run in a work tree"), even when that directory IS the
    registered project anchor -- reproduced live resuming a worktree whose
    anchor had been converted to bare. ``_git_toplevel``/``_resolve_active_project``
    must recognize this case via ``--is-bare-repository``/``--git-dir``
    instead of reporting "not inside an adopted repo".
    """
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    _git("config", "core.bare", "true", cwd=anchor)
    monkeypatch.chdir(anchor)
    assert _git_toplevel(anchor) == anchor.resolve()
    project, assumed = _resolve_active_project(None)
    assert project == "myproj"
    assert assumed is None


def test_resolve_from_worktree_cwd(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    project, assumed = _resolve_active_project(None)
    assert project == "myproj"
    assert assumed is None


def test_project_override_reports_anchor(adopted_repo: AdoptedRepo) -> None:
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    project, reported = _resolve_active_project("myproj")
    assert project == "myproj"
    # The resolver reports the anchor; main() decides whether to chdir to it.
    assert reported is not None
    assert Path(reported).resolve() == anchor.resolve()


def test_cwd_is_inside_project(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    assert _cwd_is_inside_project(anchor) is True
    monkeypatch.chdir(anchor)
    assert _cwd_is_inside_project(anchor) is True


def test_cwd_not_inside_other_project(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert _cwd_is_inside_project(_anchor) is False


def test_not_in_repo_resolves_nothing(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert _resolve_active_project(None) == (None, None)


def test_safe_cwd_survives_deleted_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vanished cwd degrades to "no project context", never a crash.

    ``os.getcwd()`` raises ``FileNotFoundError`` when the process's working
    directory has been removed out from under it (e.g. a plugin hook re-invoked
    during ``copilot plugin update``, whose payload dir Copilot has deleted).
    The CWD-based resolvers must tolerate that rather than propagate the
    exception out of ``main()`` (dotfiles#989).
    """
    def _boom() -> NoReturn:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "getcwd", _boom)

    assert _safe_cwd() is None
    assert _git_toplevel(None) is None
    assert _cwd_is_inside_project(Path("/anything")) is False
    assert _resolve_active_project(None) == (None, None)


# ---------------------------------------------------------------------------
# Worktree-id resolution is CWD-only
# ---------------------------------------------------------------------------

def test_worktree_id_from_worktree_cwd(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    monkeypatch.chdir(wt_path)
    assert worktree_identity._infer_worktree_id(None, conf) == wt_id


def test_worktree_id_none_at_anchor(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, _wt_root, _wt_path, _wt_id, conf = adopted_repo
    monkeypatch.chdir(anchor)
    # The anchor is not under worktree_root -> no worktree id.
    assert worktree_identity._infer_worktree_id(None, conf) is None


def test_worktree_id_resolves_under_foreign_worktree_root(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for copilot-extensions#59: a real, git-registered worktree must
    resolve from its CWD even when the config's ``worktree_root`` points somewhere
    else entirely (the state left behind by a worktree-root layout migration).

    The legacy single-root scan returned None here; git-based identity does not.
    """
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    # Point worktree_root at a bogus, unrelated location the worktree is NOT under.
    foreign = conf.default_repo.anchor + ".SOMEWHERE_ELSE.worktrees"
    bad_conf = dataclasses.replace(
        conf,
        repos={
            conf.repo_name: dataclasses.replace(
                conf.default_repo, worktree_root=foreign
            )
        },
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: bad_conf)
    monkeypatch.chdir(wt_path)
    # Legacy root scan would fail (cwd not under foreign root); git identity wins.
    assert worktree_identity._infer_worktree_id_from_worktree_root(
        bad_conf, Path(wt_path)
    ) is None
    assert worktree_identity._infer_worktree_id(None, bad_conf) == wt_id


def test_worktree_id_auto_adopts_untracked_linked_worktree(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A linked worktree that `git worktree add`-ed directly -- never through
    `agent-worktrees create` -- must still resolve AND get a tracking record
    written on first use, not just report git's raw identity. This is what lets
    a worktree created by an external host (a GitHub-App/coding-agent session,
    a hand-run git command, any environment without our sessionStart hook)
    still bind PR ownership on a *later* `create-pr`/`pr-status`/`finalize` call
    from a machine that DOES have agent-worktrees, instead of staying
    permanently "detached" from tracking."""
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    tdir = Path(cfg.tracking_dir())
    yaml_path = tdir / f"{wt_id}.yaml"
    assert yaml_path.exists()  # fixture pre-seeds it
    yaml_path.unlink()  # simulate: never registered via agent-worktrees create

    monkeypatch.chdir(wt_path)
    assert not yaml_path.exists()
    assert worktree_identity._infer_worktree_id(None, conf) == wt_id
    # The call must have created a real tracking record, not merely returned
    # git's raw id without persisting anything.
    assert yaml_path.exists()


def test_project_override_yields_no_worktree_id_at_anchor(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, _wt_root, _wt_path, _wt_id, conf = adopted_repo
    # After main() chdir's to the anchor for a cross-project --project call, the
    # CWD is the anchor (not under worktree_root) -> no worktree id.
    monkeypatch.chdir(anchor)
    assert worktree_identity._infer_worktree_id(None, conf) is None


# ---------------------------------------------------------------------------
# Anti-contamination: ambient env is ignored when CWD is authoritative
# ---------------------------------------------------------------------------

def test_worktree_id_ignores_wrong_env(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WORKTREE_ID / WORKTREE_REPO set to WRONG values must not override the
    worktree id resolved from the current directory."""
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    monkeypatch.setenv("WORKTREE_ID", "some-other-worktree")
    monkeypatch.setenv("WORKTREE_REPO", "/nonexistent/other/repo")
    monkeypatch.chdir(wt_path)
    assert worktree_identity._infer_worktree_id(None, conf) == wt_id


def test_project_resolution_ignores_wrong_env(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale WORKTREE_PROJECT in the env must not steer resolution when the
    directory identifies a different (correct) project."""
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.setenv("WORKTREE_PROJECT", "some-other-project")
    monkeypatch.chdir(wt_path)
    project, _assumed = _resolve_active_project(None)
    assert project == "myproj"


# ---------------------------------------------------------------------------
# `main()` chdir behavior for --project (git `-C` semantics)
# ---------------------------------------------------------------------------

def test_project_binstub_from_within_worktree_keeps_worktree(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (the note): `<project> <cmd>` run from inside one of the
    project's own worktrees must act on THAT worktree -- NOT chdir to the anchor
    and lose it. This is the common sign-off case (`<project> push-changes`)."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)

    captured: StatusCapture = {}

    def fake_status(args: argparse.Namespace) -> int:
        captured["cwd"] = Path.cwd().resolve()
        captured["wt"] = worktree_identity._infer_worktree_id_from_cwd()
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "status", fake_status)
    orig = Path.cwd()
    try:
        rc = m.main(["--project", "myproj", "status"])
    finally:
        os.chdir(orig)
    assert rc == 0
    assert captured["cwd"] == wt_path.resolve()  # did NOT chdir away
    assert captured["wt"] == wt_id               # acts on the current worktree


def test_project_binstub_from_outside_chdirs_to_anchor(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`<project> <cmd>` run from an unrelated directory chdir's to the
    project's anchor (git `-C`), so it cleanly targets its own project."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)

    captured: dict[str, Path] = {}

    def fake_status(args: argparse.Namespace) -> int:
        captured["cwd"] = Path.cwd().resolve()
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "status", fake_status)
    orig = Path.cwd()
    try:
        rc = m.main(["--project", "myproj", "status"])
    finally:
        os.chdir(orig)
    assert rc == 0
    assert captured["cwd"] == anchor.resolve()  # chdir'd to the anchor


# ---------------------------------------------------------------------------
# `get` keys: the rename-swap (worktree-dir = CURRENT worktree; worktrees-root
# = the parent directory that holds all worktrees). See the
# agent-worktrees-normalized-launch effort, Phase 1.
# ---------------------------------------------------------------------------

@pytest.fixture
def active_myproj(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set the module-level active project the way main() does, then restore."""
    cfg.set_active_project("myproj")
    yield
    cfg.set_active_project(None)


def test_get_worktree_dir_is_current_worktree(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`get worktree-dir` from inside a worktree yields THAT worktree's root."""
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(argparse.Namespace(key="worktree-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == wt_path.resolve()


def test_get_worktree_dir_uses_actual_foreign_worktree_path(
    adopted_repo: AdoptedRepo, active_myproj: None, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A host-created linked worktree must not be projected under worktree_root."""
    anchor, wt_root, _wt_path, _wt_id, _conf = adopted_repo
    foreign = tmp_path / "copilot-worktrees" / "app-session"
    foreign.parent.mkdir()
    git_ops.git(
        "worktree", "add", str(foreign), "-b", "app-session", "master",
        cwd=str(anchor),
    )
    monkeypatch.chdir(foreign)

    rc = m.cmd_get(argparse.Namespace(key="worktree-dir"))

    assert rc == 0
    assert Path(capsys.readouterr().out.strip()).resolve() == foreign.resolve()
    assert not (wt_root / "app-session").exists()


def test_get_worktree_dir_empty_at_anchor(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """At the anchor (not inside a worktree) `get worktree-dir` is empty."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(anchor)
    rc = m.cmd_get(argparse.Namespace(key="worktree-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == ""


def test_get_worktree_id_is_current_worktree(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`get worktree-id` from inside a worktree yields THAT worktree's id --
    resolvable by the setup launcher (Stage 3, copilot_invoked) without a
    session/worktree-id argument."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(argparse.Namespace(key="worktree-id"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == wt_id


def test_get_worktree_id_empty_at_anchor(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """At the anchor (not inside a worktree) `get worktree-id` is empty."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(anchor)
    rc = m.cmd_get(argparse.Namespace(key="worktree-id"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == ""


def test_get_session_scope_id_is_worktree_id_inside_a_worktree(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Inside a worktree, `session-scope-id` is exactly the worktree id --
    the CLI-mode registration extension threads this straight through as
    `worktree_id`, so it must be identical to what `get worktree-id` itself
    reports for the ordinary (non-anchor) case."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(argparse.Namespace(key="session-scope-id"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == wt_id


def test_get_session_scope_id_is_anchor_repo_name_at_anchor(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """At the anchor, `session-scope-id` is `anchor-<repo_name>` -- unlike
    `worktree-id` (deliberately left empty there, an unrelated documented
    contract), this is the new identity `agent-worktrees embody/copilot
    --anchor` and the CLI-mode registration extension actually need: without
    it, an anchor-mode session's self-registration reports a null
    worktree_id and agent-bridge's CLI-mode reservation can never correlate
    it (agent-bridge-cli-mode-sessions Phase 4 follow-up)."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(anchor)
    rc = m.cmd_get(argparse.Namespace(key="session-scope-id"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "anchor-myproj"


def test_get_worktree_state_dir_uses_machine_local_anchor_scope(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """An adopted anchor gets stable machine-local state without becoming a
    linked worktree or writing continuity artifacts into the checkout."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    project_dir = tmp_path / "machine-state" / "myproj"
    monkeypatch.setattr(cfg, "project_dir", lambda *a, **k: project_dir)
    monkeypatch.chdir(anchor)

    rc = m.cmd_get(argparse.Namespace(
        key="worktree-state-dir", session_id=None,
    ))
    out = capsys.readouterr().out.strip()

    assert rc == 0
    assert Path(out) == project_dir / "worktrees" / "@anchor"
    assert not Path(out).is_relative_to(anchor)
    assert Path(out).is_dir()


def test_get_worktree_dir_empty_outside_repo(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Outside any managed repo/worktree `get worktree-dir` is empty."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    rc = m.cmd_get(argparse.Namespace(key="worktree-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == ""


def test_get_worktree_state_dir_empty_outside_repo(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside-state"
    outside.mkdir()
    monkeypatch.chdir(outside)
    rc = m.cmd_get(argparse.Namespace(
        key="worktree-state-dir", session_id=None,
    ))
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_get_worktree_dir_binding_first_from_session(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """#4098 binding-first: cwd is outside any worktree (HOME under bare resume),
    but --session-id resolves the worktree from the session->worktree registry."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(home)
    # No scoped binding env in this test -> falls through to the registry scan.
    monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_session",
        lambda sid: wt_id if sid == "sess-1" else None)
    rc = m.cmd_get(argparse.Namespace(key="worktree-dir", session_id="sess-1"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == wt_path.resolve()


def test_get_worktree_dir_scoped_binding_preferred(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The scoped bare-resume binding wins before the registry scan runs."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    home = tmp_path / "home2"
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setattr(m, "_activate_session_binding", lambda sid: wt_id)

    def _boom(sid: str) -> NoReturn:  # pragma: no cover - must not run when binding resolves
        raise AssertionError("registry scan should not run")
    monkeypatch.setattr(tracking, "find_worktree_id_by_session", _boom)
    rc = m.cmd_get(argparse.Namespace(key="worktree-dir", session_id="sess-1"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == wt_path.resolve()


def test_get_worktree_dir_session_unresolvable_stays_empty(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A --session-id that resolves no worktree leaves worktree-dir empty (no
    guess), same as without the flag."""
    home = tmp_path / "home3"
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
    monkeypatch.setattr(tracking, "find_worktree_id_by_session",
                        lambda sid: None)
    rc = m.cmd_get(argparse.Namespace(key="worktree-dir", session_id="ghost"))
    assert capsys.readouterr().out.strip() == "" and rc == 0


def test_get_worktree_state_dir_resolves_anchor_from_session_cwd(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import context_cli

    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    home = tmp_path / "home-anchor-session"
    home.mkdir()
    project_dir = tmp_path / "machine-state" / "myproj"
    monkeypatch.chdir(home)
    monkeypatch.setattr(cfg, "project_dir", lambda *a, **k: project_dir)
    monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_session", lambda sid: None,
    )
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_cwd", lambda cwd: None,
    )
    monkeypatch.setattr(sessions, "session_cwd", lambda sid: anchor)
    monkeypatch.setattr(context_cli, "no_window_kwargs", lambda: {"creationflags": 123})
    real_run = subprocess.run
    probes: list[dict[str, object]] = []

    # Preserve subprocess.run's open keyword surface while observing one Git probe.
    def capture_probe(
        command: list[str], *args: Any, **kwargs: Any,
    ) -> subprocess.CompletedProcess[bytes | str]:
        if command == ["git", "-C", str(anchor), "rev-parse", "--show-toplevel"]:
            if kwargs.get("creationflags") == 123:
                probes.append(kwargs.copy())
                kwargs.pop("creationflags")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture_probe)

    rc = m.cmd_get(argparse.Namespace(
        key="worktree-state-dir", session_id="anchor-session",
    ))
    out = capsys.readouterr().out.strip()

    assert rc == 0
    assert Path(out) == project_dir / "worktrees" / "@anchor"
    assert Path(out).is_dir()
    assert probes
    assert probes[-1]["creationflags"] == 123


def test_related_anchor_suppresses_git_probe_console(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import related_cli

    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(related_cli, "no_window_kwargs", lambda: {"creationflags": 123})

    def capture_probe(
        command: list[str], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode=0, stdout=f"{tmp_path}\n")

    monkeypatch.setattr(subprocess, "run", capture_probe)
    assert related_cli._related_anchor([]) == str(tmp_path)
    assert calls[0][0] == ["git", "rev-parse", "--show-toplevel"]
    assert calls[0][1]["creationflags"] == 123


def test_get_worktree_state_dir_falls_back_to_the_callers_project(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A session started outside any adopted repo (its recorded cwd names no
    project) resolves from where the caller runs, as every project-scoped
    command does: here, the worktree it is invoked from."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    unadopted = tmp_path / "not-adopted"
    unadopted.mkdir()
    git_ops.git("init", "-q", cwd=str(unadopted))  # a checkout, just not an adopted one
    project_dir = tmp_path / "machine-state" / "myproj"
    cfg.set_active_project(None)
    monkeypatch.chdir(wt_path)

    def machine_project_dir(name: str | None = None) -> Path:
        if (name or cfg.active_project()) != "myproj":  # as the real one: no project, no directory
            raise RuntimeError("No active project could be resolved.")
        return project_dir

    monkeypatch.setattr(cfg, "project_dir", machine_project_dir)
    monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
    monkeypatch.setattr(tracking, "find_worktree_id_by_session", lambda sid: None)
    monkeypatch.setattr(tracking, "find_worktree_id_by_cwd", lambda cwd: None)
    monkeypatch.setattr(sessions, "session_cwd", lambda sid: unadopted)
    try:
        rc = m.main(["get", "worktree-state-dir", "--session-id", "outside-session"])
    finally:
        cfg.set_active_project(None)
    out = capsys.readouterr().out.strip()

    assert rc == 0
    assert Path(out) == project_dir / "worktrees" / wt_id


def test_get_worktree_state_dir_rejects_unknown_session(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home-unknown-session"
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setattr(m, "_activate_session_binding", lambda sid: None)
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_session", lambda sid: None,
    )
    monkeypatch.setattr(sessions, "session_cwd", lambda sid: None)

    rc = m.cmd_get(argparse.Namespace(
        key="worktree-state-dir", session_id="ghost",
    ))

    assert rc == 1
    assert "unknown or is not associated" in capsys.readouterr().out


def test_get_worktrees_root_is_parent(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`get worktrees-root` yields the parent dir that holds all worktrees --
    the OLD meaning of `worktree-dir` -- regardless of CWD."""
    _anchor, wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(argparse.Namespace(key="worktrees-root"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == wt_root.resolve()


def test_get_repo_dir_is_anchor(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`get repo-dir` still yields the anchor repo, from inside a worktree."""
    anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(argparse.Namespace(key="repo-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == anchor.resolve()


@pytest.mark.parametrize("record_machine", ["friendly-host", "friendly-host-wsl"])
def test_get_owner_ref_uses_receiving_execution_identity(
    adopted_repo: AdoptedRepo, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], record_machine: str,
) -> None:
    from agent_worktrees import execution_spaces

    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    record_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    record_path.write_text(json.dumps({
        "worktree_id": wt_id, "branch": f"worktree/{wt_id}", "worktree_path": str(wt_path),
        "repo": conf.repo_name, "machine": record_machine, "platform": conf.platform,
    }), encoding="utf-8")
    before = record_path.read_bytes()
    monkeypatch.chdir(wt_path)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", conf.repo_name)
    monkeypatch.setattr(socket, "gethostname", lambda: "receiving-host")
    monkeypatch.setattr(execution_spaces, "require_current_execution_space", lambda config: None)
    def unused_path(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("owner-ref must not resolve an unused checkout path")

    monkeypatch.setattr(m, "_worktree_path_for_id", unused_path)
    assert conf.machine != record_machine
    assert m.cmd_get(argparse.Namespace(key="owner-ref")) == 0
    assert capsys.readouterr().out.strip() == f"receiving-host/{conf.repo_name}/{wt_id}"
    assert record_path.read_bytes() == before


def test_get_keys_lists_swapped_keys(
    adopted_repo: AdoptedRepo, capsys: pytest.CaptureFixture[str],
) -> None:
    """`get keys` advertises both the repointed worktree-dir and worktrees-root."""
    rc = m.cmd_get(argparse.Namespace(key="keys"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "worktree-dir" in out
    assert "worktrees-root" in out


@pytest.mark.parametrize("remote,expected", [("origin", "origin"), ("upstream", "upstream"), ("", "origin")])
def test_get_git_remote_uses_native_repository_config(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], remote: str, expected: str,
) -> None:
    _anchor, _wt_root, wt_path, _wt_id, conf = adopted_repo
    monkeypatch.chdir(wt_path)
    monkeypatch.setitem(conf.repos, conf.repo_name, dataclasses.replace(conf.default_repo, remote=remote))
    assert m.cmd_get(argparse.Namespace(key="git-remote")) == 0
    assert capsys.readouterr().out.strip() == expected


def test_get_project_scalars_skip_worktree_identity(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    anchor, wt_root, wt_path, _wt_id, conf = adopted_repo
    monkeypatch.chdir(wt_path)
    config_dir = wt_root / "config"
    monkeypatch.setattr(cfg, "project_dir", lambda: config_dir)
    calls: list[bool] = []

    def load_config(
        *_args: object, include_control_plane_related_pr: bool, **kwargs: object,
    ) -> cfg.Config:
        calls.append(include_control_plane_related_pr)
        return conf

    def unexpected_lookup(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("project-only scalars must not scan worktree identity")

    monkeypatch.setattr(cfg, "load_config", load_config)
    monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", unexpected_lookup)
    monkeypatch.setattr(m, "_worktree_path_for_id", unexpected_lookup)
    monkeypatch.setattr(m, "_cwd_is_inside_project", unexpected_lookup)
    monkeypatch.setattr(
        m, "_resolve_repo_remote", lambda config, repo: "https://example.com/myrepo.git",
    )
    monkeypatch.setattr(m, "_resolve_lease_origin", lambda: "local")
    expected: dict[str, str | Path] = {
        "repo-dir": anchor,
        "git-remote": conf.default_repo.remote,
        "worktrees-root": wt_root,
        "src-dir": conf.srcroot,
        "config-dir": config_dir,
        "machine": conf.machine,
        "platform": conf.platform,
        "project": conf.repo_name,
        "repo-remote": "https://example.com/myrepo.git",
        "lease-origin": "local",
        "pr-enabled": "true" if conf.default_repo.pr.enabled else "false",
        "pr-required": "true" if conf.default_repo.pr.required else "false",
        "pr-provider": conf.default_repo.pr.provider if conf.default_repo.pr.enabled else "",
        "pr-profile": m._pr_flow_profile(conf.default_repo).profile,
    }
    for key, value in expected.items():
        assert m.cmd_get(argparse.Namespace(key=key)) == 0
        assert capsys.readouterr().out.strip() == str(value)
    assert calls == [key.startswith("pr-") for key in expected]


@pytest.mark.parametrize("key", ["worktree-id", "session-scope-id"])
def test_get_identity_scalars_skip_checkout_path(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], key: str,
) -> None:
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    subdir = wt_path / "nested"
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    def unused_path(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("identity scalar must not resolve an unused checkout path")

    monkeypatch.setattr(m, "_worktree_path_for_id", unused_path)
    assert m.cmd_get(argparse.Namespace(key=key)) == 0
    assert capsys.readouterr().out.strip() == wt_id


@pytest.mark.parametrize("key", ["machine", "repo-remote", "pr-provider"])
def test_get_project_scalar_preserves_explicit_session_target(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], key: str,
) -> None:
    _anchor, _wt_root, _wt_path, _wt_id, conf = adopted_repo
    calls: list[tuple[str, str | bool]] = []

    def activate_session(session_id: str) -> str:
        calls.append(("session", session_id))
        return "target-worktree"

    def load_config(
        *_args: object, include_control_plane_related_pr: bool, **kwargs: object,
    ) -> cfg.Config:
        calls.append(("config", include_control_plane_related_pr))
        return conf

    def unexpected_lookup(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("scalar must not resolve checkout after project activation")

    monkeypatch.setattr(m, "_activate_session_binding", activate_session)
    monkeypatch.setattr(cfg, "load_config", load_config)
    monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", unexpected_lookup)
    monkeypatch.setattr(m, "_worktree_path_for_id", unexpected_lookup)

    def remote(config: cfg.Config, repo: cfg.RepoConfig) -> str:
        assert config is conf and repo is conf.default_repo
        return "https://example.com/target.git"

    monkeypatch.setattr(m, "_resolve_repo_remote", remote)
    expected = {
        "machine": conf.machine,
        "repo-remote": "https://example.com/target.git",
        "pr-provider": conf.default_repo.pr.provider if conf.default_repo.pr.enabled else "",
    }
    assert m.cmd_get(argparse.Namespace(key=key, session_id="target-session")) == 0
    assert capsys.readouterr().out.strip() == expected[key]
    assert calls == [("session", "target-session"), ("config", key.startswith("pr-"))]


def test_get_non_pr_key_skips_control_plane_related_pr(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression (copilot-extensions#2660): ``_control_plane_related_pr_map``
    is a 13-subprocess, ~218-file-read walk over every installed plugin, and
    it is only ever consulted for the ``pr-*`` keys' values -- every other
    ``get`` key must skip it (``include_control_plane_related_pr=False``)
    rather than pay that cost on every single invocation regardless of the
    key actually requested."""
    _anchor, _wt_root, wt_path, _wt_id, conf = adopted_repo
    monkeypatch.chdir(wt_path)
    calls: list[bool] = []

    def spy_load_config(
        *a: object, include_control_plane_related_pr: bool = True, **k: object,
    ) -> cfg.Config:
        calls.append(include_control_plane_related_pr)
        return conf

    monkeypatch.setattr(cfg, "load_config", spy_load_config)

    rc = m.cmd_get(argparse.Namespace(key="worktree-dir"))
    assert rc == 0
    assert calls == [False]


def test_get_pr_key_still_includes_control_plane_related_pr(
    adopted_repo: AdoptedRepo, active_myproj: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The four ``pr-*`` keys DO need the control-plane PR-graft overlay, so
    they must keep requesting it."""
    _anchor, _wt_root, wt_path, _wt_id, conf = adopted_repo
    monkeypatch.chdir(wt_path)
    calls: list[bool] = []

    def spy_load_config(
        *a: object, include_control_plane_related_pr: bool = True, **k: object,
    ) -> cfg.Config:
        calls.append(include_control_plane_related_pr)
        return conf

    monkeypatch.setattr(cfg, "load_config", spy_load_config)

    for key in ("pr-enabled", "pr-required", "pr-provider", "pr-profile"):
        calls.clear()
        rc = m.cmd_get(argparse.Namespace(key=key))
        assert rc == 0
        assert calls == [True], key


def test_picker_paths_json_reports_install_and_plugin_roots(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / ".agent-worktrees"
    home = tmp_path / "home"
    monkeypatch.setattr(cfg, "install_dir", lambda: install_dir)
    monkeypatch.setattr(cfg, "_home", lambda: home)

    rc = m.cmd_picker_paths(argparse.Namespace(json=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload == {
        "version": 1,
        "install_dir": str(install_dir),
        "installed_plugins_dir": str(home / ".copilot" / "installed-plugins"),
    }


def test_picker_bootstrap_json_reports_runner_decisions(
    adopted_repo: AdoptedRepo,
    active_myproj: None,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.setattr(m, "_resolve_active_project", lambda project: (project, anchor))
    monkeypatch.setattr(m, "_cwd_is_inside_project", lambda candidate: False)
    monkeypatch.setattr(m, "_in_ssh_session", lambda: True)

    rc = m.cmd_picker_bootstrap(argparse.Namespace(json=True))

    payload = json.loads(capfd.readouterr().out)
    assert rc == 0
    assert payload == {
        "version": 1,
        "project": "myproj",
        "should_switch_cwd": True,
        "cwd": str(anchor.resolve()),
        "workspace": None,
        "default_live": False,
        "project_config": {"config_dir": str(cfg.project_dir("myproj"))},
    }


def test_repair_stale_anchor_json_reports_targeted_status(
    adopted_repo: AdoptedRepo,
    active_myproj: None,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _anchor, _wt_root, _wt_path, _wt_id, conf = adopted_repo
    states = iter((False, True))

    def _present(_config: cfg.Config) -> bool:
        return next(states)

    monkeypatch.setattr("agent_worktrees.update_runtime._self_entry_present", _present)
    monkeypatch.setattr(
        "agent_worktrees.update_runtime._heal_stale_anchor_if_self_missing",
        lambda config: config,
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: conf)

    rc = m.cmd_repair_stale_anchor(argparse.Namespace(json=True))

    payload = json.loads(capfd.readouterr().out)
    assert rc == 0
    assert payload == {
        "version": 1,
        "project": "myproj",
        "status": "repaired",
        "self_present_before": False,
        "self_present_after": True,
    }
