from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_worktrees.context_cli import cmd_knowledge_dispatch
from agent_worktrees import control_plane_cli


def _topology(path: Path, *, project: str = "example-harness") -> None:
    path.write_text(
        "control_plane:\n"
        f"  project: {project}\n"
        "  harness: example-harness\n"
        "  knowledge: example-knowledge\n"
        "machines: {}\n",
        encoding="utf-8",
    )


def test_native_reader_retains_pair_without_machine_local_configuration(tmp_path, capsys):
    topology = tmp_path / "machines.yaml"
    _topology(topology)
    before = topology.read_bytes()
    assert cmd_knowledge_dispatch(
        ["control-plane", "--path", str(topology), "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["version"] == 1
    assert result["platform"] in {"windows", "wsl", "linux"}
    assert result["source"] == str(topology.resolve())
    assert result["control_plane"] == {
        "harness": "example-harness",
        "knowledge": "example-knowledge",
        "legacy_project": "example-harness",
        "paired": True,
        "legacy_compatible": True,
    }
    assert topology.read_bytes() == before
    assert list(tmp_path.iterdir()) == [topology]


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_reader_carries_shared_native_platform_without_environment_guessing(
    tmp_path, capsys, monkeypatch, platform
):
    topology = tmp_path / "machines.yaml"
    _topology(topology)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(control_plane_cli, "detect_platform", lambda: platform)
    assert cmd_knowledge_dispatch(
        ["control-plane", "--path", str(topology), "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["platform"] == platform


def test_real_native_front_door_reads_pair_in_unconfigured_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    topology = tmp_path / "machines.yaml"
    _topology(topology)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "AGENT_WORKTREES_PROJECT": "unregistered",
    }
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_worktrees",
            "knowledge",
            "control-plane",
            "--path",
            str(topology),
            "--json",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        **kwargs,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["control_plane"]["knowledge"] == "example-knowledge"
    assert not (home / ".example-harness" / "config.yaml").exists()


@pytest.mark.parametrize("missing", [False, True])
def test_native_reader_reports_conflicting_or_missing_source(tmp_path, capsys, missing):
    topology = tmp_path / "machines.yaml"
    if not missing:
        _topology(topology, project="example-knowledge")
    assert cmd_knowledge_dispatch(
        ["control-plane", "--path", str(topology), "--json"]
    ) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["version"] == 1
    assert result["error"]
    assert "control_plane" not in result


@pytest.mark.parametrize(
    "name", ["123", "true", "NO", "on", "null", "2026-10-18", "123-knowledge", "1e3"]
)
def test_binding_projection_preserves_repository_names_as_native_yaml_strings(name):
    script = (
        Path(__file__).resolve().parents[3]
        / "plugins" / "harness-knowledge" / "skills" / "binding-knowledge"
        / "scripts" / "bind_knowledge.py"
    )
    spec = importlib.util.spec_from_file_location("scalar_binding_owner", script)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    text = owner.set_top_yaml_key("knowledge_repo: old\n", "knowledge_repo", name)
    parsed = yaml.safe_load(text)
    assert type(parsed["knowledge_repo"]) is str
    assert parsed["knowledge_repo"] == name
    plain = f"knowledge_repo: {name}\n"
    if isinstance(yaml.safe_load(plain)["knowledge_repo"], str):
        assert owner._check_existing_binding(plain, name)


def test_binding_update_preserves_native_indentless_sequence_structure(tmp_path):
    script = (
        Path(__file__).resolve().parents[3]
        / "plugins" / "harness-knowledge" / "skills" / "binding-knowledge"
        / "scripts" / "binding_yaml.py"
    )
    spec = importlib.util.spec_from_file_location("native_sequence_binding", script)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    path = tmp_path / "config.yaml"
    existing = "repo_name: harness\ncopilot_profiles:\n-\n  name: cloud\n  model: example\n"
    path.write_text(existing, encoding="utf-8")
    owner.write_binding(path, "harness", "knowledge", preserve=True)
    before, after = yaml.safe_load(existing), yaml.safe_load(path.read_text())
    assert after == {**before, "knowledge_repo": "knowledge"}
