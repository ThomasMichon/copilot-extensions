"""Opt-in real registered-ID -> selected binding payload -> pointer/readback flow."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs
from agent_procutil import owned_process
import pytest
import yaml

from agent_worktrees import config as cfg, project_state, repos
from agent_worktrees.repos_configuration_sources import ConfigurationError


@pytest.mark.timeout(75)
@pytest.mark.parametrize(("change_machine", "accepted_default"), [
    (False, None), (True, None), (False, "unchanged"), (False, "changed"), (False, "config-changed"),
])
def test_fresh_portable_mapping_uses_real_native_binding_transaction(
    tmp_path, monkeypatch, capsys, change_machine, accepted_default,
):
    for key in ("AGENT_HOME", "HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    calls = []
    original_run = owned_process.run_caller_owned
    state = tmp_path / "accepted-state"
    machines_python = os.environ.get("KNOWLEDGE_PREFERENCE_TEST_PYTHON", "")
    if accepted_default:
        if not machines_python:
            pytest.skip("select a non-editable Machines test artifact for the accepted-default flow")
        assert Path(machines_python).is_file(), "select the packaged Machines test interpreter"
        from agent_worktrees import _peer_launch, machines_runtime
        prefix = [machines_python, "-I", "-B", "-X", "utf8", "-m", "agent_machines"]
        monkeypatch.setattr(machines_runtime, "selected", lambda **k: (
            prefix, _peer_launch.legacy_peer_environment(dict(os.environ)), Path(machines_python),
        ))

    def measured(argv, **kwargs):
        phase = "capabilities" if "--capabilities" in argv else "preview" if "--configuration" in argv else "apply"
        if change_machine and phase == "apply" and "apply" in argv:
            cfg.global_config_path().write_text("machine: changed-worker\n", encoding="utf-8")
        if accepted_default and "agent_worktrees.repos_knowledge_configuration" in argv and "apply" in argv:
            worker = """
import json, os, runpy, sys
from pathlib import Path
from agent_worktrees import _peer_launch, machines_runtime, repos_knowledge_default
python, state, change, *arguments = sys.argv[1:]
machines_runtime.selected = lambda **kw: (
    [python, '-I', '-B', '-X', 'utf8', '-m', 'agent_machines'],
    _peer_launch.legacy_peer_environment(dict(os.environ)), Path(python),
)
if change == 'changed':
    original = repos_knowledge_default.revalidate
    def revalidate(project, selection):
        path = Path(state) / 'accepted.json'
        path.write_bytes(path.read_bytes() + b'\\n')
        original(project, selection)
    repos_knowledge_default.revalidate = revalidate
elif change == 'config-changed':
    original = repos_knowledge_default._read
    def read(*args):
        result = original(*args)
        path = Path(state).parent / ('.' + arguments[0]) / 'config.yaml'
        path.write_bytes(path.read_bytes() + b'headless: true\\n')
        return result
    repos_knowledge_default._read = read
sys.argv = ['agent_worktrees.repos_knowledge_configuration', *arguments]
runpy.run_module('agent_worktrees.repos_knowledge_configuration', run_name='__main__')
"""
            index = argv.index("agent_worktrees.repos_knowledge_configuration")
            argv = [
                sys.executable, "-I", "-B", "-c", worker, machines_python, str(state),
                accepted_default, *argv[index + 1:],
            ]
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
            "repo_name": project,
            "repos": {project: {"anchor": str(roots[project]), "worktree_root": str(tmp_path / "trees")}},
        }), encoding="utf-8")
        cfg.global_config_path().write_text("machine: example-worker\n", encoding="utf-8")
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
        if accepted_default:
            state.mkdir()
            (state / "policy.json").write_text(json.dumps({
                "schema": "agent-machines.preferences.policy", "version": 1,
                "publisher": "example-publisher", "participant": "example-receiver", "domains": ["repositories"],
            }), encoding="utf-8")
            (state / "accepted.json").write_text(json.dumps({
                "schema": "agent-machines.preferences", "version": 1,
                "publisher": "example-publisher", "revision": 1, "parent": None,
                "domains": {"repositories": {"knowledge": [
                    {"harness_repository_id": args[3], "knowledge_repository_id": args[5]},
                ]}},
            }), encoding="utf-8")
            document = yaml.safe_load(config_path.read_text())
            document["knowledge_preferences"] = {"state_dir": str(state)}
            config_path.write_text(yaml.safe_dump(document), encoding="utf-8")
            args.append("--accepted-default")
            from agent_worktrees.repos_configuration_catalog import catalog

            listing = catalog(project)
            row = next(row for row in listing["entries"] if row["repository"] == "example-knowledge")
            assert row["preferred_knowledge_bindable"] == "true", listing["summary"]
            assert row["harness_repository_id"] == args[3] and row["knowledge_repository_id"] == args[5]
            decoder_source = (
                Path(__file__).resolve().parents[4] / "worktree-manager" / "src"
                / "worktree_manager" / "configuration_preview.py"
            )
            spec = importlib.util.spec_from_file_location("_fixture_manager_preview", decoder_source)
            decoder = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, spec.name, decoder)
            spec.loader.exec_module(decoder)
        before_registry = registry.read_bytes()
        before_config = config_path.read_bytes()
        before_state = {path.name: path.read_bytes() for path in state.iterdir()} if accepted_default else {}
        from agent_worktrees import repos_knowledge_preferences
        from agent_worktrees import repos_configuration

        try:
            if accepted_default:
                assert repos_configuration.command(["knowledge-preview", *args]) == 0
                proposed = json.loads(capsys.readouterr().out)
            else:
                proposed = repos_knowledge_preferences.preview(project, args[3], args[5])
        except ConfigurationError as error:
            pytest.fail(f"{error.code}; cause={type(error.__cause__).__name__}; stages={calls}", pytrace=False)
        if accepted_default:
            consumed = decoder.parse_configuration_preview(json.dumps(proposed), row["configuration"]["target"])
            assert consumed.token == proposed["token"] and not consumed.blocked
            assert consumed.operation == "set"
        else:
            assert proposed["status"] == "suggested"
            assert proposed["execution_machine"] == "example-worker"
            assert proposed["native_preview"]["operation"] == "set"
        assert config_path.read_bytes() == before_config
        if accepted_default:
            assert {path.name: path.read_bytes() for path in state.iterdir()} == before_state
        try:
            if accepted_default:
                code = repos_configuration.command([
                    "knowledge-apply", *args, "--preview-token", proposed["token"],
                ])
                result = json.loads(capsys.readouterr().out)
                if accepted_default in {"changed", "config-changed"}:
                    assert code == 1 and result["code"] == "binding-owner-refused", (result, calls)
                    assert config_path.read_bytes() == before_config + (
                        b"headless: true\n" if accepted_default == "config-changed" else b""
                    )
                    assert registry.read_bytes() == before_registry
                    assert not (tmp_path / "trees").exists()
                    return
                assert code == 0, (result, calls)
            else:
                result = repos_knowledge_preferences.apply(project, args[3], args[5], proposed["token"])
        except ConfigurationError as error:
            if change_machine:
                assert error.code == "binding-owner-refused", calls
                assert config_path.read_bytes() == before_config
                assert registry.read_bytes() == before_registry
                assert not (tmp_path / "trees").exists()
                return
            pytest.fail(f"{error.code}; cause={type(error.__cause__).__name__}; stages={calls}", pytrace=False)
        assert not change_machine, "changed machine scope committed"
        assert result["status"] == "persisted" and result["pointer_committed"]
        assert result["registration_status"] == result["state_root_status"] == "ready"
        assert yaml.safe_load(config_path.read_text())["knowledge_repo"] == "example-knowledge"
        assert registry.read_bytes() == before_registry
        assert not (tmp_path / "trees").exists()
    finally:
        cfg.set_active_project(previous)
