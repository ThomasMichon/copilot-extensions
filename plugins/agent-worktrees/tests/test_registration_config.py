"""Preserve user-owned machine settings when registration refreshes its config."""

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import __main__ as main
from agent_worktrees import installation_cli


def test_reregister_preserves_binding_and_nested_overrides(tmp_path):
    path = tmp_path / "config.yaml"
    original = {
        "repo_name": "example",
        "knowledge_repo": "example-knowledge",
        "headless": True,
        "terminal_profiles": ["custom"],
        "custom": {"enabled": False},
        "repos": {
            "example": {
                "anchor": "old-anchor",
                "remote": "upstream",
                "worktree_root": "custom-worktrees",
                "custom": {"retain": True},
            },
            "other": {"anchor": "other-anchor", "remote": "other-remote"},
        },
    }
    path.write_text(yaml.safe_dump(original), encoding="utf-8")

    main._write_config(path, tmp_path / "anchor", "machine", "windows", "example")

    updated = yaml.safe_load(path.read_text(encoding="utf-8"))
    original["repos"]["example"]["anchor"] = str(tmp_path / "anchor")
    assert updated == original


def test_reregister_does_not_mutate_shared_yaml_aliases(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "shared: &shared\n"
        "  anchor: old-anchor\n"
        "  remote: upstream\n"
        "repos:\n"
        "  example: *shared\n"
        "  other: *shared\n",
        encoding="utf-8",
    )

    main._write_config(path, tmp_path / "anchor", "machine", "windows", "example")

    updated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert updated["repos"]["example"]["anchor"] == str(tmp_path / "anchor")
    assert updated["repos"]["other"] == {
        "anchor": "old-anchor",
        "remote": "upstream",
    }


@pytest.mark.parametrize("exists", [False, True])
def test_explicit_adoption_options_are_applied(tmp_path, exists):
    path = tmp_path / "config.yaml"
    if exists:
        path.write_text("knowledge_repo: example-knowledge\n", encoding="utf-8")

    main._write_config(
        path, tmp_path / "anchor", "machine", "windows", "example",
        headless=True, no_terminal_profile=True,
    )

    updated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert updated["repo_name"] == "example"
    assert updated["repos"]["example"] == {
        "anchor": str(tmp_path / "anchor"), "remote": "origin",
    }
    assert updated["headless"] is True
    assert updated["terminal_profiles"] == []
    if exists:
        assert updated["knowledge_repo"] == "example-knowledge"


@pytest.mark.parametrize("content", [
    "knowledge_repo: [unterminated",
    "- not-a-mapping\n",
    "repos: []\n",
    "repos:\n  example: false\n",
])
def test_invalid_existing_config_is_not_overwritten(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises((ValueError, yaml.YAMLError)):
        main._write_config(path, tmp_path / "anchor", "machine", "windows", "example")

    assert path.read_text(encoding="utf-8") == content


def test_empty_existing_config_can_be_registered(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")

    main._write_config(path, tmp_path / "anchor", "machine", "linux", "example")

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "repo_name": "example",
        "repos": {"example": {"anchor": str(tmp_path / "anchor"), "remote": "origin"}},
    }


def test_registration_writer_preserves_binding_in_fresh_process(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("knowledge_repo: example-knowledge\n", encoding="utf-8")
    source = Path(main.__file__).resolve().parents[1]
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agent_worktrees.__main__ as main
main._write_config(Path(sys.argv[2]), Path(sys.argv[3]), "machine", "linux", "example")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(source), str(path), str(tmp_path / "anchor")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["knowledge_repo"] == "example-knowledge"


@pytest.mark.parametrize(
    ("no_agent", "expected_profiles"),
    [(False, ["custom"]), (True, [])],
)
def test_forced_registration_only_clears_profiles_for_explicit_no_agent(
    monkeypatch, tmp_path, no_agent, expected_profiles,
):
    from agent_worktrees import installer, picker_profiles_cli, repos

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.yaml"
    config_path.write_text("terminal_profiles:\n  - custom\n", encoding="utf-8")

    monkeypatch.setattr(installation_cli.cfg, "project_dir", lambda name: config_dir)
    monkeypatch.setattr(installation_cli.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(
        installation_cli.cfg, "machines_yaml_path", lambda path: repo / "missing.yaml",
    )
    monkeypatch.setattr(
        installation_cli.cfg,
        "load_config",
        lambda *a, **k: SimpleNamespace(
            default_repo=SimpleNamespace(pr=SimpleNamespace(enabled=False)),
        ),
    )
    overrides = {
        "_prepare_namespaced_project_state": lambda *a, **k: True,
        "_write_global_config": lambda *a, **k: None,
        "_cleanup_stale_instructions": lambda *a, **k: None,
        "_ensure_ado_pr_cli": lambda *a, **k: None,
    }
    monkeypatch.setattr(
        installation_cli,
        "_core_helper",
        lambda name, local: overrides.get(name, local),
    )
    existing_repo = SimpleNamespace(
        agent=False,
        repo_class="worktree",
        account="",
    )
    monkeypatch.setattr(repos, "find_repo", lambda *a, **k: existing_repo)
    monkeypatch.setattr(repos, "add_repo", lambda *a, **k: None)
    monkeypatch.setattr(installer, "register_project", lambda *a, **k: None)
    monkeypatch.setattr(installer, "deploy_binstubs", lambda *a, **k: True)
    monkeypatch.setattr(
        picker_profiles_cli, "_refresh_terminal_profiles", lambda *a, **k: True,
    )

    @contextmanager
    def registration(*args, **kwargs):
        yield SimpleNamespace(commit=lambda: None)

    monkeypatch.setattr(installer, "project_binstub_registration", registration)
    monkeypatch.setattr(
        installation_cli.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    args = SimpleNamespace(
        project_name="example",
        repo_dir=str(repo),
        machine="example-host",
        default_branch="main",
        force=True,
        headless=False,
        no_agent=no_agent,
        agent=False,
        base_repo=False,
        elevated=False,
    )

    assert installation_cli.cmd_register(args) == 0
    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["terminal_profiles"] == expected_profiles
