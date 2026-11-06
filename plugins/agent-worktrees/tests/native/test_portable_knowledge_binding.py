"""Opt-in real registered-ID -> selected binding payload -> pointer/readback flow."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time

from agent_procutil import no_window_kwargs
from agent_procutil import owned_process
import pytest
import yaml

from agent_worktrees import config as cfg, project_state, repos
from agent_worktrees.repos_configuration_sources import ConfigurationError


@pytest.mark.timeout(75)
def test_fresh_portable_mapping_uses_real_native_binding_transaction(tmp_path, monkeypatch):
    for key in ("AGENT_HOME", "HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    calls = []
    original_run = owned_process.run_caller_owned

    def measured(argv, **kwargs):
        phase = "capabilities" if "--capabilities" in argv else "preview" if "--configuration" in argv else "apply"
        started = time.monotonic()
        try:
            result = original_run(argv, **kwargs)
        except Exception as error:
            calls.append((phase, round(time.monotonic() - started, 3), type(error).__name__))
            raise
        calls.append((
            phase, round(time.monotonic() - started, 3), result.returncode,
            str(result.stderr)[-1200:] if result.returncode else "",
        ))
        return result

    monkeypatch.setattr(owned_process, "run_caller_owned", measured)
    project = "example-harness"
    previous = cfg.active_project()
    cfg.set_active_project(project)
    try:
        roots = {}
        for name in (project, "example-knowledge"):
            root = tmp_path / name
            child = subprocess.run(
                ["git", "-c", "init.defaultBranch=main", "init", "--quiet", str(root)],
                capture_output=True, timeout=5, **no_window_kwargs(),
            )
            assert child.returncode == 0, child.stderr
            child = subprocess.run(
                ["git", "-C", str(root), "remote", "add", "origin", f"https://git.example.test/team/{name}.git"],
                capture_output=True, timeout=5, **no_window_kwargs(),
            )
            assert child.returncode == 0, child.stderr
            roots[name] = root
        policy = roots[project] / ".agent-worktrees"
        policy.mkdir()
        (policy / "config.yaml").write_text(
            "stateless: true\nrequires_external_state_root: true\n", encoding="utf-8",
        )
        registry = repos._repos_yaml_path()
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(yaml.safe_dump({"repos": {
            name: {
                "class": "worktree" if name == project else "knowledge",
                repos._current_platform(): str(root), "remote": f"https://git.example.test/team/{name}.git",
                "default_branch": "main", "account": "example-account",
            } for name, root in roots.items()
        }}), encoding="utf-8")
        local = tmp_path / ".example-harness"
        local.mkdir()
        config_path = local / "config.yaml"
        config_path.write_text(yaml.safe_dump({
            "repo_name": project, "machine": "example-worker",
            "repos": {project: {"anchor": str(roots[project]), "worktree_root": str(tmp_path / "trees")}},
        }), encoding="utf-8")
        (registry.parent / "projects.yaml").write_text(yaml.safe_dump({
            "schema_version": 2, "projects": {project: {"config_dir": str(local)}},
        }), encoding="utf-8")
        source = Path(__file__).resolve().parents[4] / "plugins" / "harness-knowledge"
        payload = tmp_path / ".copilot" / "installed-plugins" / "fixture" / "harness-knowledge"
        shutil.copytree(source, payload, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        (tmp_path / ".copilot" / "settings.json").write_text(json.dumps({
            "enabledPlugins": {"harness-knowledge@fixture": True},
        }), encoding="utf-8")
        args = [
            "--context-project", project,
            "--harness-id", project_state.canonical_repository_id(project),
            "--knowledge-id", project_state.canonical_repository_id("example-knowledge"),
        ]
        before_registry = registry.read_bytes()
        before_config = config_path.read_bytes()
        from agent_worktrees import repos_knowledge_preferences

        try:
            proposed = repos_knowledge_preferences.preview(project, args[3], args[5])
        except ConfigurationError as error:
            pytest.fail(f"{error.code}; cause={type(error.__cause__).__name__}; stages={calls}", pytrace=False)
        assert proposed["status"] == "suggested"
        assert proposed["native_preview"]["operation"] == "set"
        assert config_path.read_bytes() == before_config
        try:
            result = repos_knowledge_preferences.apply(project, args[3], args[5], proposed["token"])
        except ConfigurationError as error:
            pytest.fail(f"{error.code}; cause={type(error.__cause__).__name__}; stages={calls}", pytrace=False)
        assert result["status"] == "persisted" and result["pointer_committed"]
        assert result["registration_status"] == result["state_root_status"] == "ready"
        assert yaml.safe_load(config_path.read_text())["knowledge_repo"] == "example-knowledge"
        assert registry.read_bytes() == before_registry
        assert not (tmp_path / "trees").exists()
    finally:
        cfg.set_active_project(previous)
