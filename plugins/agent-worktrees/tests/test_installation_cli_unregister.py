"""Tests for cmd_uninstall's shared-registry safety gate and the new
cmd_unregister project-deregistration verb.

See ThomasMichon/copilot-extensions#4520: `uninstall --project X
--remove-config` deletes the SHARED ~/.agent-worktrees registry root
(config.yaml, repos.yaml, projects.yaml, accounts.yaml, snapshots/,
pivots/) -- used by every adopted project on the machine, not only the one
named -- with no confirmation and no scoped alternative. This module covers
the two requested fixes: a --yes-gated dry run before that shared deletion
(naming the other adopted projects that would lose their registry entries),
and a project-scoped `unregister` verb that touches only its own
projects.yaml/repos.yaml entries and binstub.
"""

from __future__ import annotations

import argparse

import pytest

from agent_worktrees import installation_cli as installation_cli_mod
from agent_worktrees import repos as repos_mod
from agent_worktrees import unregister_cli as unregister_cli_mod


def _uninstall_args(*, remove_config=False, yes=False):
    return argparse.Namespace(remove_config=remove_config, yes=yes)


def _unregister_args(*, keep_repo_entry=False, force=False):
    return argparse.Namespace(keep_repo_entry=keep_repo_entry, force=force)


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    """Isolate cmd_uninstall/cmd_unregister from any real machine state."""
    shared = tmp_path / "shared"
    monkeypatch.setattr(installation_cli_mod.cfg, "project_name", lambda: "demoproj")
    monkeypatch.setattr(
        installation_cli_mod.cfg, "project_dir", lambda *a, **k: tmp_path / ".demoproj"
    )
    monkeypatch.setattr(
        installation_cli_mod.cfg,
        "tracking_dir",
        lambda: tmp_path / ".demoproj" / "worktrees",
    )
    monkeypatch.setattr(installation_cli_mod.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(installation_cli_mod.inst, "install_dir", lambda: shared)
    monkeypatch.setattr(installation_cli_mod.inst, "bin_dir", lambda: shared / "bin")
    monkeypatch.setattr(installation_cli_mod.inst, "venv_dir", lambda: shared / ".venv")
    monkeypatch.setattr(installation_cli_mod.inst, "lib_dir", lambda: shared / "lib")
    monkeypatch.setattr(installation_cli_mod.inst, "remove_project_binstub", lambda project: [])

    registry = {"projects": {"demoproj": {}, "otherproj": {}}}
    monkeypatch.setattr(
        installation_cli_mod.inst,
        "read_projects_registry",
        lambda: {"projects": dict(registry["projects"])},
    )

    def _write(reg, path=None):
        registry["projects"] = dict(reg.get("projects", {}))

    monkeypatch.setattr(installation_cli_mod.inst, "write_projects_registry", _write)
    return {"shared": shared, "registry": registry, "tmp_path": tmp_path}


# ── uninstall --remove-config safety gate ───────────────────────────────


def test_uninstall_remove_config_without_yes_is_dry_run(fake_runtime, capsys):
    shared = fake_runtime["shared"]
    shared.mkdir(parents=True)
    (shared / "config.yaml").write_text("x")

    rc = installation_cli_mod.cmd_uninstall(_uninstall_args(remove_config=True, yes=False))

    assert rc == 0
    assert shared.exists()  # never deleted without --yes
    out = capsys.readouterr().out
    assert "otherproj" in out  # names the other adopted project sharing the root
    assert "--yes" in out


def test_uninstall_remove_config_with_yes_deletes_shared_root(fake_runtime):
    shared = fake_runtime["shared"]
    shared.mkdir(parents=True)
    (shared / "config.yaml").write_text("x")

    rc = installation_cli_mod.cmd_uninstall(_uninstall_args(remove_config=True, yes=True))

    assert rc == 0
    assert not shared.exists()


def test_uninstall_without_remove_config_never_touches_shared_registry(fake_runtime):
    shared = fake_runtime["shared"]
    shared.mkdir(parents=True)
    (shared / "repos.yaml").write_text("x")
    (shared / "deploy-manifest.json").write_text("{}")

    rc = installation_cli_mod.cmd_uninstall(_uninstall_args(remove_config=False))

    assert rc == 0
    assert (shared / "repos.yaml").exists()
    assert not (shared / "deploy-manifest.json").exists()


# ── unregister ────────────────────────────────────────────────────────────


def test_unregister_removes_projects_yaml_entry_only(fake_runtime, monkeypatch):
    monkeypatch.setattr(repos_mod, "remove_repo", lambda name: True)

    rc = installation_cli_mod.cmd_unregister(_unregister_args())

    assert rc == 0
    assert "demoproj" not in fake_runtime["registry"]["projects"]
    assert "otherproj" in fake_runtime["registry"]["projects"]
    assert not fake_runtime["shared"].exists()  # shared runtime untouched


def test_unregister_keep_repo_entry_downgrades_instead_of_removing(fake_runtime, monkeypatch):
    calls: dict = {}

    class FakeEntry:
        remote = "https://github.com/example/demoproj.git"
        default_branch = "main"

        def local_path(self, plat):
            return "C:/src/demoproj"

    monkeypatch.setattr(repos_mod, "find_repo", lambda name: FakeEntry())

    def fake_add_repo(name, path, **kwargs):
        calls["name"] = name
        calls["path"] = path
        calls["kwargs"] = kwargs

    monkeypatch.setattr(repos_mod, "add_repo", fake_add_repo)
    remove_called: list[str] = []
    monkeypatch.setattr(
        repos_mod, "remove_repo", lambda name: remove_called.append(name) or True
    )

    rc = installation_cli_mod.cmd_unregister(_unregister_args(keep_repo_entry=True))

    assert rc == 0
    assert calls["name"] == "demoproj"
    assert calls["kwargs"]["repo_class"] == "reference"
    assert not remove_called


def test_unregister_refuses_with_live_worktrees_unless_forced(fake_runtime, monkeypatch):
    monkeypatch.setattr(
        unregister_cli_mod, "_unregister_blockers", lambda project: ["1 live worktree(s)"]
    )
    monkeypatch.setattr(repos_mod, "remove_repo", lambda name: True)

    rc = installation_cli_mod.cmd_unregister(_unregister_args(force=False))
    assert rc == 1
    assert "demoproj" in fake_runtime["registry"]["projects"]  # untouched

    rc2 = installation_cli_mod.cmd_unregister(_unregister_args(force=True))
    assert rc2 == 0
    assert "demoproj" not in fake_runtime["registry"]["projects"]


def test_unregister_never_touches_other_projects_or_shared_root(fake_runtime, monkeypatch):
    shared = fake_runtime["shared"]
    shared.mkdir(parents=True)
    (shared / "config.yaml").write_text("x")
    (shared / "repos.yaml").write_text("x")
    monkeypatch.setattr(repos_mod, "remove_repo", lambda name: True)

    rc = installation_cli_mod.cmd_unregister(_unregister_args())

    assert rc == 0
    assert (shared / "config.yaml").exists()
    assert (shared / "repos.yaml").exists()
    assert "otherproj" in fake_runtime["registry"]["projects"]
