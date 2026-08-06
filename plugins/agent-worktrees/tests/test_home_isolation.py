"""Regression: the test suite must never touch real ~/.agent-worktrees state.

aperture-labs #4349: a test that reached a real registry *writer* (e.g.
``reconcile_binstubs`` -> ``prune_reserved_projects`` -> ``write_projects_registry``)
while patching only the reader clobbered the developer's real
``~/.agent-worktrees/projects.yaml``. The autouse ``_isolate_agent_worktrees_home``
fixture redirects HOME so no test can escape to real state. These tests prove
the isolation is in force for BOTH registry paths (config._home / Path.home).
"""
from __future__ import annotations

from pathlib import Path

from agent_worktrees import config as cfg
from agent_worktrees import installer as inst
from agent_worktrees import repos


def test_home_is_redirected_under_tmp():
    """Path.home() and USERPROFILE resolve to the throwaway fixture home."""
    home = Path.home()
    # A real developer home would contain e.g. the checkout drive; the fixture
    # home is a fresh tmp dir named ``aw-home*`` (tmp_path_factory adds a counter).
    assert home.name.startswith("aw-home")
    assert cfg._home() == home


def test_projects_registry_writes_under_isolated_home():
    """A real ``write_projects_registry`` lands under the isolated home, never
    the developer's real ``~/.agent-worktrees/projects.yaml`` (#4349)."""
    target = inst.projects_yaml_path()
    assert Path.home() in target.parents, (
        f"projects.yaml would be written to {target}, outside the isolated home")

    inst.write_projects_registry({"schema_version": 2, "projects": {"x": {}}})
    assert target.exists()
    # The write stayed inside the fixture's tmp home.
    assert Path.home() in target.parents


def test_repos_registry_path_under_isolated_home():
    """repos.yaml resolves via Path.home() directly -- also isolated (#4349)."""
    path = repos._repos_yaml_path()
    assert Path.home() in path.parents


def test_reconcile_binstubs_writer_cannot_escape(monkeypatch, tmp_path):
    """The exact shape that caused the incident: reconcile_binstubs -> prune ->
    write. Even patching only the reader, the write must stay isolated."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    # Reader patched to include the reserved name (prune will write a change).
    monkeypatch.setattr(
        inst, "read_projects_registry",
        lambda: {"projects": {"agent-worktrees": {}, "realproj": {}}})

    inst.reconcile_binstubs()

    # Whatever got written landed under the isolated home, not real state.
    written = inst.projects_yaml_path()
    assert Path.home() in written.parents
