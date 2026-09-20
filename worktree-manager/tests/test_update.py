"""Tests for the Manager's `update` command — the plugin updater/aligner seam.

`<project> update` hands off to `worktree-manager update`, which (1) self-updates
the Manager and (2) orchestrates the harness update by driving the engine's own
mechanics via `agent-worktrees update --no-manager` (the seam bypass). These pin
that sequencing, the flag forwarding, the bypass boundary, and self_update's
best-effort behavior — all without a real engine or network.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stdout

import pytest

from worktree_manager import __main__ as wm
from worktree_manager import engine_client as ec
from worktree_manager import self_install


def _run_update(rest, monkeypatch, *, su_action="already-current", su_kwargs=None):
    from worktree_manager.self_install import SelfUpdateResult
    calls = {}
    monkeypatch.setattr(
        wm, "_cmd_update", wm._cmd_update)  # ensure real function under test

    def fake_self_update(**kw):
        calls["self_update"] = kw
        return SelfUpdateResult(action=su_action, version="0.1.0-dev9",
                                previous="0.1.0-dev8", **(su_kwargs or {}))

    monkeypatch.setattr(self_install, "self_update", fake_self_update)

    def fake_passthrough(project, args, **kw):
        calls["passthrough"] = {"project": project, "args": args}
        return 0

    monkeypatch.setattr(ec, "run_engine_passthrough", fake_passthrough)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wm._cmd_update(rest)
    return rc, calls, buf.getvalue()


def test_update_self_updates_then_orchestrates_bypass(monkeypatch):
    rc, calls, out = _run_update([], monkeypatch)
    assert rc == 0
    # Self-update ran first…
    assert "self_update" in calls
    # …then the harness update was driven through the engine with the bypass flag.
    assert calls["passthrough"]["project"] is None
    assert calls["passthrough"]["args"] == ["update", "--no-manager"]


def test_update_forwards_project_and_flags(monkeypatch):
    rc, calls, out = _run_update(
        ["--force", "--project", "dotfiles", "--skip-modules", "agent-bridge"],
        monkeypatch)
    assert rc == 0
    # --project is forwarded through run_engine_passthrough's own project param
    # (placed before the verb), not appended as a flag after it; other flags
    # forwarded after the bypass.
    assert calls["passthrough"]["project"] == "dotfiles"
    assert calls["passthrough"]["args"] == [
        "update", "--no-manager", "--force", "--skip-modules", "agent-bridge"]


def test_update_reports_self_update_and_continues(monkeypatch):
    rc, calls, out = _run_update([], monkeypatch, su_action="updated")
    assert rc == 0
    assert "updated" in out and "active on next run" in out
    assert "passthrough" in calls  # continues to the harness update regardless


def test_update_engine_absent_hints_setup(monkeypatch):
    from worktree_manager.self_install import SelfUpdateResult
    monkeypatch.setattr(self_install, "self_update",
                        lambda **kw: SelfUpdateResult(action="skipped", reason="git not found"))

    def boom(project, args, **kw):
        raise ec.EngineError("not installed", install_hint=True)

    monkeypatch.setattr(ec, "run_engine_passthrough", boom)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wm._cmd_update([])
    assert rc == 1
    assert "worktree-manager setup" in buf.getvalue()


def test_extract_project_pulls_pair_and_leaves_rest():
    project, rest = wm._extract_project(["--force", "--project", "x", "--skip-modules"])
    assert project == "x"
    assert rest == ["--force", "--skip-modules"]


def test_extract_project_none_when_absent():
    project, rest = wm._extract_project(["--force", "--skip-modules"])
    assert project is None
    assert rest == ["--force", "--skip-modules"]


# ── run_engine_passthrough ────────────────────────────────────────────────────

def test_passthrough_builds_command_and_returns_code(monkeypatch):
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )
    ec.set_engine_command(None)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    rc = ec.run_engine_passthrough(None, ["update", "--no-manager"])
    assert rc == 0
    assert seen["cmd"][-2:] == ["update", "--no-manager"]
    assert "/fake/agent-worktrees" in seen["cmd"][0]


def test_passthrough_absent_engine_hints_install(monkeypatch):
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "installed_engine_command", lambda: None)
    ec.set_engine_command(None)
    with pytest.raises(ec.EngineError) as ei:
        ec.run_engine_passthrough(None, ["update"])
    assert ei.value.install_hint is True


# ── self_update (best-effort, git-fetch + version-install) ────────────────────

def test_self_update_without_git_non_github_skips(monkeypatch, tmp_path):
    """Without git AND a non-GitHub source, there is no tarball endpoint — skip."""
    from worktree_manager import source_config as sc
    monkeypatch.setattr(self_install.shutil, "which", lambda name: None)
    sc.set_source(repo=str(tmp_path / "local-remote"), root=tmp_path)  # non-GitHub
    res = self_install.self_update(root=tmp_path, dry_run=True)
    assert res.action == "skipped"
    assert "git" in (res.reason or "")


def test_self_update_without_git_falls_back_to_tarball(monkeypatch, tmp_path):
    """Without git but a GitHub source, self_update fetches the codeload tarball."""
    from worktree_manager import source_config as sc
    monkeypatch.setattr(self_install.shutil, "which", lambda name: None)
    monkeypatch.setattr(self_install, "local_bin", lambda: tmp_path / "localbin")
    sc.set_source(repo="https://github.com/acme/widgets.git", ref="main", root=tmp_path)
    seen = {}

    def fake_fetch(staging, url, **kw):
        seen["url"] = url
        pkg = staging / "worktree-manager" / "src" / "worktree_manager"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text('__version__ = "9.9.9"\n')
        (staging / "worktree-manager" / "pyproject.toml").write_text(
            "[project]\nname='x'\nversion='9.9.9'\n")

    monkeypatch.setattr(self_install, "_fetch_via_tarball", fake_fetch)
    res = self_install.self_update(root=tmp_path, dry_run=False)
    assert seen["url"] == "https://codeload.github.com/acme/widgets/tar.gz/main"
    assert res.action == "updated"
    assert res.version == "9.9.9"


@pytest.mark.parametrize("repo,ref,expected", [
    ("https://github.com/ThomasMichon/copilot-extensions.git", "main",
     "https://codeload.github.com/ThomasMichon/copilot-extensions/tar.gz/main"),
    ("https://github.com/acme/widgets", "canary",
     "https://codeload.github.com/acme/widgets/tar.gz/canary"),
    ("git@github.com:acme/widgets.git", "main",
     "https://codeload.github.com/acme/widgets/tar.gz/main"),
])
def test_manager_tarball_url_github(tmp_path, repo, ref, expected):
    from worktree_manager import source_config as sc
    sc.set_source(repo=repo, ref=ref, root=tmp_path)
    assert self_install.manager_tarball_url(tmp_path) == expected


def test_manager_tarball_url_non_github_is_none(tmp_path):
    from worktree_manager import source_config as sc
    sc.set_source(repo=str(tmp_path / "local"), root=tmp_path)
    assert self_install.manager_tarball_url(tmp_path) is None


@pytest.mark.parametrize("repo,ref,expected", [
    ("https://github.com/ThomasMichon/copilot-extensions.git", "main",
     "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main"
     "/worktree-manager/src/worktree_manager/__init__.py"),
    ("https://github.com/acme/widgets", "canary",
     "https://raw.githubusercontent.com/acme/widgets/canary"
     "/worktree-manager/src/worktree_manager/__init__.py"),
    ("git@github.com:acme/widgets.git", "main",
     "https://raw.githubusercontent.com/acme/widgets/main"
     "/worktree-manager/src/worktree_manager/__init__.py"),
])
def test_remote_init_url_github(tmp_path, repo, ref, expected):
    from worktree_manager import source_config as sc
    sc.set_source(repo=repo, ref=ref, root=tmp_path)
    assert self_install.remote_init_url(tmp_path) == expected


def test_remote_init_url_non_github_is_none(tmp_path):
    from worktree_manager import source_config as sc
    sc.set_source(repo=str(tmp_path / "local"), root=tmp_path)
    assert self_install.remote_init_url(tmp_path) is None


def test_fetch_remote_version_parses_the_fetched_init_py(monkeypatch, tmp_path):
    """A successful GET returns the parsed ``__version__`` -- no git, no
    clone/tarball, a single small file read."""
    from worktree_manager import source_config as sc

    sc.set_source(
        repo="https://github.com/acme/widgets.git", ref="main", root=tmp_path)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'__version__ = "9.9.9"\n'

    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen)
    assert self_install.fetch_remote_version(tmp_path) == "9.9.9"
    assert seen["url"] == (
        "https://raw.githubusercontent.com/acme/widgets/main"
        "/worktree-manager/src/worktree_manager/__init__.py")


def test_fetch_remote_version_degrades_to_none_on_any_failure(monkeypatch, tmp_path):
    """Network error, timeout, unparsable content -- all degrade to ``None``,
    never raise. This is the property a background poll depends on."""
    from worktree_manager import source_config as sc

    sc.set_source(
        repo="https://github.com/acme/widgets.git", ref="main", root=tmp_path)

    def boom(url, timeout=None):
        raise OSError("network is down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert self_install.fetch_remote_version(tmp_path) is None


def test_fetch_remote_version_non_github_source_is_none(tmp_path):
    from worktree_manager import source_config as sc
    sc.set_source(repo=str(tmp_path / "local"), root=tmp_path)
    assert self_install.fetch_remote_version(tmp_path) is None



def test_self_update_reports_updated(monkeypatch, tmp_path):
    from worktree_manager.self_install import SelfInstallResult
    monkeypatch.setattr(self_install.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(self_install.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    # Pretend the fetched payload exists and self_install reports an install.
    monkeypatch.setattr(self_install.Path, "is_file", lambda self: True)
    monkeypatch.setattr(self_install, "self_install",
                        lambda **kw: SelfInstallResult(version="0.1.0-dev9",
                                                       action="installed", root=str(tmp_path)))
    res = self_install.self_update(root=tmp_path, dry_run=False)
    assert res.action == "updated"
    assert res.version == "0.1.0-dev9"
