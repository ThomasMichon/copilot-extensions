"""Shared test fixtures for agent-worktrees tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent_worktrees import tracking

# ---------------------------------------------------------------------------
# Isolate the in-process active-project / assumed-CWD state between tests.
# These module globals are set by main() during CWD/--project resolution;
# without a reset a test that runs main() (or set_active_project) would leak
# its project into unrelated tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_active_project():
    import os

    from agent_worktrees import config as _cfg

    _saved = os.environ.get("WORKTREE_PROJECT")
    _cfg.set_active_project(None)
    # Also clear the WORKTREE_PROJECT env fallback that project_name() consults.
    # Otherwise a value leaked from the launching shell (or a prior test that
    # ran main()) satisfies project_name() and makes tests pass or fail
    # depending on the ambient environment / test order. Tests that need a
    # project set it explicitly (e.g. via monkeypatch_config or set_active_project).
    os.environ.pop("WORKTREE_PROJECT", None)
    yield
    _cfg.set_active_project(None)
    # main() writes WORKTREE_PROJECT into os.environ directly (for legacy shell
    # consumers); restore the pre-test value so it never leaks between tests.
    if _saved is None:
        os.environ.pop("WORKTREE_PROJECT", None)
    else:
        os.environ["WORKTREE_PROJECT"] = _saved

# ---------------------------------------------------------------------------
# Path fixtures — redirect config helpers to tmp dirs
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_pivots(tmp_path_factory):
    """Point the picker's pivot-manifest registry at an empty tmp dir so tests
    are hermetic regardless of what a dev machine has deployed under
    ``~/.agent-worktrees/pivots/`` (e.g. the agent-dispatch manifest). Tests
    that exercise discovery override this env var explicitly.

    Also isolates the marketplace plugin-install root that ``ensure_pivots``
    restores from (#2180): without it, the picker's self-heal would scan the
    real ``~/.copilot/installed-plugins`` and copy a contributed manifest back
    into the (otherwise empty) tmp pivots dir, re-introducing the very ambient
    pivot this fixture exists to suppress.

    Uses ``os.environ`` directly (not ``monkeypatch``) so this autouse fixture
    introduces no dependency that could reorder fixture teardown.
    """
    import os

    empty = tmp_path_factory.mktemp("empty-pivots")
    empty_plugins = tmp_path_factory.mktemp("empty-plugins")
    saved = os.environ.get("AGENT_WORKTREES_PIVOTS_DIR")
    saved_plugins = os.environ.get("AGENT_WORKTREES_PLUGINS_DIR")
    os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = str(empty)
    os.environ["AGENT_WORKTREES_PLUGINS_DIR"] = str(empty_plugins)
    yield
    if saved is None:
        os.environ.pop("AGENT_WORKTREES_PIVOTS_DIR", None)
    else:
        os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = saved
    if saved_plugins is None:
        os.environ.pop("AGENT_WORKTREES_PLUGINS_DIR", None)
    else:
        os.environ["AGENT_WORKTREES_PLUGINS_DIR"] = saved_plugins


@pytest.fixture
def tmp_tracking_dir(tmp_path: Path) -> Path:
    """Temporary tracking directory for worktree YAMLs."""
    d = tmp_path / "worktrees"
    d.mkdir()
    return d


@pytest.fixture
def tmp_session_state_dir(tmp_path: Path) -> Path:
    """Temporary ~/.copilot/session-state/ equivalent."""
    d = tmp_path / "session-state"
    d.mkdir()
    return d


@pytest.fixture
def monkeypatch_config(monkeypatch, tmp_path: Path, tmp_tracking_dir: Path):
    """Patch config helpers to use tmp dirs."""
    monkeypatch.setenv("WORKTREE_PROJECT", "test-project")
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tmp_tracking_dir)
    monkeypatch.setattr("agent_worktrees.config.project_dir", lambda: tmp_path / ".test-project")
    monkeypatch.setattr(
        "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees"
    )


@pytest.fixture
def sample_record(tmp_tracking_dir: Path) -> tracking.WorktreeRecord:
    """A pre-built WorktreeRecord saved to the tracking dir."""
    rec = tracking.WorktreeRecord(
        worktree_id="test-wt-001",
        branch="worktree/test-wt-001",
        worktree_path="/tmp/test-worktree",
        repo="test-repo",
        machine="test-machine",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    tracking.save_record(rec, tmp_tracking_dir / f"{rec.worktree_id}.yaml")
    return rec


def make_session_dir(
    session_state_dir: Path,
    session_id: str,
    cwd: str,
    *,
    summary: str = "",
    updated_at: str = "2026-06-01T10:00:00.000Z",
    events_lines: list[str] | None = None,
    lock_pid: int | None = None,
    has_events_file: bool = True,
    context_pct: int | None = None,
) -> Path:
    """Create a mock session directory with workspace.yaml and optional files."""
    sdir = session_state_dir / session_id
    sdir.mkdir(parents=True, exist_ok=True)

    ws_content = textwrap.dedent(f"""\
        id: {session_id}
        cwd: {cwd}
        git_root: {cwd}
        branch: main
        name: Test Session
        summary: {summary or 'Test Session'}
        created_at: {updated_at}
        updated_at: {updated_at}
    """)
    (sdir / "workspace.yaml").write_text(ws_content)

    if has_events_file:
        lines = events_lines or []
        (sdir / "events.jsonl").write_text("\n".join(lines) + "\n" if lines else "")

    if lock_pid is not None:
        (sdir / f"inuse.{lock_pid}.lock").write_text("")

    if context_pct is not None:
        import json
        (sdir / "context.json").write_text(json.dumps({
            "sessionId": session_id,
            "utilizationPct": context_pct,
            "updatedAt": updated_at,
        }))

    return sdir


# ---------------------------------------------------------------------------
# PR-mode repo fixture (shared by test_pr_ops + test_providers)
# ---------------------------------------------------------------------------

from agent_worktrees import config as cfg  # noqa: E402
from agent_worktrees import git_ops  # noqa: E402


def _pr_git(*args: str, cwd) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


@pytest.fixture
def pr_repo(tmp_path: Path, monkeypatch):
    """A bare 'remote' + anchor + a worktree branch with two commits.

    Returns (config, worktree_id, worktree_path, remote_dir).  Patches
    tracking_dir so records land in a tmp directory.  PR mode is enabled with
    ``auto_open=False`` so create_pr exercises only the git side unless a test
    opts in.
    """
    remote_dir = tmp_path / "remote.git"
    anchor = tmp_path / "anchor"
    wt_root = tmp_path / "worktrees"
    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()

    git_ops.git("init", "--bare", "-b", "master", str(remote_dir))

    git_ops.git("init", "-b", "master", str(anchor))
    _pr_git("config", "user.email", "t@example.com", cwd=anchor)
    _pr_git("config", "user.name", "Test", cwd=anchor)
    (anchor / "README.md").write_text("base\n")
    _pr_git("add", "-A", cwd=anchor)
    _pr_git("commit", "-m", "initial", cwd=anchor)
    _pr_git("remote", "add", "origin", str(remote_dir), cwd=anchor)
    _pr_git("push", "-u", "origin", "master", cwd=anchor)

    worktree_id = "test-wt-20260618-aaaa"
    wt_path = wt_root / worktree_id
    wt_root.mkdir(parents=True, exist_ok=True)
    git_ops.git(
        "worktree", "add", str(wt_path), "-b", f"worktree/{worktree_id}",
        "origin/master", cwd=str(anchor),
    )
    _pr_git("config", "user.email", "t@example.com", cwd=wt_path)
    _pr_git("config", "user.name", "Test", cwd=wt_path)
    (wt_path / "a.txt").write_text("one\n")
    _pr_git("add", "-A", cwd=wt_path)
    _pr_git("commit", "-m", "work 1", cwd=wt_path)
    (wt_path / "b.txt").write_text("two\n")
    _pr_git("add", "-A", cwd=wt_path)
    _pr_git("commit", "-m", "work 2", cwd=wt_path)

    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(wt_root),
            default_branch="master", remote="origin",
            # Pin the legacy ``snapshot`` scheme here so the base fixture keeps
            # exercising it explicitly. The default is now ``refspec`` (#1815);
            # refspec is covered by the ``_refspec_config`` tests + the config
            # default tests, which override/assert the scheme directly.
            pr=cfg.PRConfig(enabled=True, provider="gitea", branch_prefix="feature",
                            head_scheme="snapshot", auto_open=False),
        )},
    )

    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tracking_d)
    # pr_ops helpers that are called without an explicit ``config`` fall back to
    # ``cfg.load_config()``, which resolves the on-disk config for the active
    # project. In tests there is no active project, so pin load_config to this
    # fixture's config -- otherwise the call raises (no project) or, worse,
    # silently reads a real ~/.<project>/config.yaml when the launching shell
    # leaked WORKTREE_PROJECT, making these tests order/environment dependent.
    monkeypatch.setattr("agent_worktrees.config.load_config", lambda *a, **k: config)

    tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(wt_path),
        "ext", "test", "linux", tracking_d,
    )

    return config, worktree_id, wt_path, remote_dir
