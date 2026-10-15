"""Narrow profile integration retains shortcuts and avoids repeated discovery."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import local_profiles as profiles, terminal_fragment as tf


def test_refresh_collects_once_and_reuses_inputs(monkeypatch, tmp_path):
    inputs = [tf.ProjectInput(name="demo", display="Demo")]
    calls = []
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    (tmp_path / ".demo").mkdir()
    (tmp_path / ".demo" / "config.yaml").write_text("machine: host\n", encoding="utf-8")
    monkeypatch.setattr(
        tf, "collect_local_projects",
        lambda **k: calls.append(("collect", k)) or inputs,
    )

    def deploy(machine, **kwargs):
        assert machine == "host"
        assert kwargs == {"current_project": "demo", "apply": True, "projects": inputs}
        calls.append(("deploy", kwargs))
        return SimpleNamespace(
            applied=True, fragment_path=tmp_path / "fragment.json",
            new_guids=["profile"], notes=[],
        )

    monkeypatch.setattr(tf, "deploy_fragment", deploy)
    monkeypatch.setattr(profiles, "refresh_shortcuts", lambda rows: rows is inputs and 0)
    result = profiles.refresh("demo")
    assert [name for name, _ in calls] == ["collect", "deploy"]
    assert result["status"] == "verified_current"
    assert result["profiles"] == 1


def test_missing_fragment_destination_is_not_success(monkeypatch):
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tf, "collect_local_projects", lambda **k: [])
    monkeypatch.setattr(tf, "deploy_fragment", lambda *a, **k: SimpleNamespace(applied=False))
    with pytest.raises(RuntimeError, match="destination"):
        profiles.refresh("demo", "host")


def test_cli_reports_blocked_shortcuts_as_json(monkeypatch, capsys):
    def blocked(*args):
        raise RuntimeError("shortcut denied")

    monkeypatch.setattr(profiles, "refresh", blocked)
    assert profiles.main(["demo"]) == 1
    response = json.loads(capsys.readouterr().out)
    assert response["schema"] == profiles.SCHEMA
    assert response["status"] == "blocked"
    assert "shortcut denied" in response["error"]


def test_shortcut_payload_uses_stdin_and_one_headless_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles.shutil, "which", lambda name: "powershell")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    captured = []

    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"changed": 0}', stderr="")

    monkeypatch.setattr(profiles.subprocess, "run", run)
    assert profiles.refresh_shortcuts([
        tf.ProjectInput("demo", "Demo", wsl_distro="Example", wsl_state="ready"),
    ]) == 0
    assert len(captured) == 1
    argv, kwargs = captured[0]
    data = json.loads(kwargs["input"])
    assert data["projects"] == [{"display": "Demo", "wsl": True}]
    assert str(tmp_path) not in " ".join(argv)
    assert kwargs["timeout"] == 30
    assert "CreateShortcut" in argv[-1]
    assert "if ($link.TargetPath" in argv[-1]


@pytest.mark.parametrize("display", ['bad/name', 'bad"name', "bad\nname"])
def test_invalid_shortcut_name_is_refused(monkeypatch, tmp_path, display):
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles.shutil, "which", lambda name: "powershell")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    with pytest.raises(ValueError, match="display name"):
        profiles.refresh_shortcuts([tf.ProjectInput("demo", display)])


def test_unchanged_fragment_is_not_rewritten(tmp_path):
    path = tmp_path / "fragment.json"
    fragment = {"profiles": [], "schemes": []}
    path.write_text(json.dumps(fragment), encoding="utf-8")
    before = path.stat().st_mtime_ns
    plan = tf.FragmentDeployPlan(
        fragment_path=path, old_guids=[], new_guids=[], changed_guids=[],
        stale_guids=[], generated_plan=None, settings_stale_count=0,
        wt_running=False, fragment=fragment, notes=[],
    )
    tf._apply_deploy_plan(plan, None)
    assert path.stat().st_mtime_ns == before
    assert json.loads(path.read_text(encoding="utf-8")) == fragment


def test_native_shortcut_second_pass_preserves_mtime(tmp_path, monkeypatch):
    import platform
    if platform.system() != "Windows":
        pytest.skip("Windows Shell COM integration")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    inputs = [tf.ProjectInput("demo", "Demo")]
    assert profiles.refresh_shortcuts(inputs) == 1
    link = tmp_path / ".local" / "bin" / "Demo.lnk"
    before = link.stat().st_mtime_ns
    assert profiles.refresh_shortcuts(inputs) == 0
    assert link.stat().st_mtime_ns == before


def test_native_shortcut_retains_wsl_upgrade_and_retirement(tmp_path, monkeypatch):
    import platform
    if platform.system() != "Windows":
        pytest.skip("Windows Shell COM integration")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "Demo (WSL").write_bytes(b"")
    (local_bin / "Foreign (WSL").write_bytes(b"")
    inputs = [tf.ProjectInput("demo", "Demo", wsl_distro="Example", wsl_state="ready")]
    assert profiles.refresh_shortcuts(inputs) == 3
    assert (local_bin / "Demo (WSL).lnk").is_file()
    assert not (local_bin / "Demo (WSL").exists()
    assert (local_bin / "Foreign (WSL").is_file()
    assert profiles.refresh_shortcuts([tf.ProjectInput("demo", "Demo")]) == 1
    assert not (local_bin / "Demo (WSL).lnk").exists()
    assert (local_bin / "Demo.lnk").is_file()


@pytest.mark.parametrize("project", ["../escape", "..", "bad/name", "bad\\name"])
def test_invalid_project_name_never_reaches_config(monkeypatch, project):
    def unexpected(*args):
        pytest.fail("Invalid project reached configuration lookup")

    monkeypatch.setattr(profiles.harness_state, "project_config", unexpected)
    with pytest.raises(ValueError, match="project name"):
        profiles.refresh(project)


@pytest.mark.parametrize(("relative", "text"), [
    (".agent-worktrees/projects.yaml", "projects: ["),
    (".agent-worktrees/projects.yaml", "projects: []"),
    (".agent-worktrees/projects.yaml", "projects:\n  demo: invalid"),
    (".agent-worktrees/repos.yaml", "repos: []"),
    (".demo/config.yaml", "terminal_profiles: invalid"),
    (".demo/config.yaml", "terminal_profiles:\n- machine: host\n"),
])
def test_invalid_inputs_do_not_rewrite_profiles(monkeypatch, tmp_path, relative, text):
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input reached profile publication")

    monkeypatch.setattr(tf, "deploy_fragment", unexpected)
    with pytest.raises(ValueError):
        profiles.refresh("demo", "host")


def test_native_refresh_command_twice_without_installer(tmp_path, monkeypatch):
    import platform
    if platform.system() != "Windows":
        pytest.skip("Windows Shell COM integration")
    home = tmp_path / "home"
    (home / ".demo").mkdir(parents=True)
    (home / ".demo" / "config.yaml").write_text("machine: example-host\n", encoding="utf-8")
    local = home / "local"
    local.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "LOCALAPPDATA": str(local)}
    observations = []
    for _ in range(2):
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "worktree_manager", "refresh-profiles", "demo"],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, **profiles.no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr + result.stdout
        response = json.loads(result.stdout)
        assert response["status"] == "verified_current"
        observations.append(elapsed)
        path = Path(response["fragment"])
        if len(observations) == 1:
            initial = path.stat().st_mtime_ns
        else:
            assert path.stat().st_mtime_ns == initial
            assert response["shortcuts_changed"] == 0
    print("native profile command elapsed_seconds=" + repr(observations))
