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
    (".agent-worktrees/repos.yaml", "repos:\n  demo: invalid"),
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


@pytest.mark.parametrize("selection", [
    [{"machine": " ", "env": "Win", "kind": "shell"}],
    [{"machine": "host", "env": " ", "kind": "shell"}],
    [{"machine": 7, "env": "Win", "kind": "shell"}],
    [{"machine": "host", "env": "Win", "kind": "shel"}],
    [{"machine": "host", "env": "Win", "kind": None}],
])
def test_invalid_selection_values_preserve_outputs(monkeypatch, tmp_path, selection):
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    (tmp_path / ".demo").mkdir()
    (tmp_path / ".demo" / "config.yaml").write_text(
        json.dumps({"machine": "host", "terminal_profiles": selection}), encoding="utf-8",
    )
    existing = tmp_path / "existing-fragment.json"
    existing.write_text('{"profiles": ["retained"]}', encoding="utf-8")
    before = existing.read_bytes(), existing.stat().st_mtime_ns

    def unexpected(*args, **kwargs):
        existing.write_text("overwritten", encoding="utf-8")
        pytest.fail("Invalid selection reached publishing")

    monkeypatch.setattr(tf, "deploy_fragment", unexpected)
    with pytest.raises(ValueError, match="Invalid terminal_profiles"):
        profiles.refresh("demo")
    assert (existing.read_bytes(), existing.stat().st_mtime_ns) == before


def test_collection_revalidates_selection_without_tolerant_coercion(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    (tmp_path / ".demo").mkdir()
    config = tmp_path / ".demo" / "config.yaml"
    config.write_text("machine: host\n", encoding="utf-8")
    collect = tf.collect_local_projects

    def changed(**kwargs):
        config.write_text("machine: host\nterminal_profiles:\n- {machine: host, env: Win, kind: shel}\n",
                          encoding="utf-8")
        return collect(**kwargs)

    monkeypatch.setattr(tf, "collect_local_projects", changed)
    with pytest.raises(ValueError, match="kind must be"):
        profiles.refresh("demo")


@pytest.mark.parametrize(("relative", "damage"), [
    (".agent-worktrees/projects.yaml", "symlink"),
    (".agent-worktrees/repos.yaml", "symlink"),
    (".demo/config.yaml", "symlink"),
    *[(f".agent-worktrees/{kind}.yaml", damage)
      for kind in ("projects", "repos")
      for damage in ("yaml", "denied", "entry")],
])
def test_strict_collection_failures_preserve_published_outputs(
    monkeypatch, tmp_path, capsys, relative, damage,
):
    home = tmp_path / "home"
    registry_dir = home / ".agent-worktrees"
    registry_dir.mkdir(parents=True)
    (registry_dir / "projects.yaml").write_text("projects:\n  demo: {}\n  peer: {}\n", encoding="utf-8")
    (registry_dir / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    (home / ".demo").mkdir()
    (home / ".demo" / "config.yaml").write_text("machine: host\n", encoding="utf-8")
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: home)
    fragments, state = tmp_path / "fragments", tmp_path / "state"
    (fragments / "AgentWorktrees").mkdir(parents=True)
    state.mkdir()
    guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    outputs = {
        fragments / "AgentWorktrees" / "agent-worktrees.json":
            {"profiles": [{"guid": guid, "name": "Peer", "commandline": "ssh example"}]},
        state / "state.json": {"generatedProfiles": [guid]},
        state / "settings.json": {"profiles": {"list": [{"guid": guid, "source": "AgentWorktrees"}]}},
    }
    for path, data in outputs.items():
        path.write_text(json.dumps(data), encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in outputs}
    monkeypatch.setattr(tf, "_wt_fragments_dir", lambda: fragments)
    monkeypatch.setattr(tf, "_wt_local_state_dir", lambda: state)
    monkeypatch.setattr(profiles, "refresh_shortcuts", lambda *_: pytest.fail("reached shortcuts"))
    target = home / relative
    if damage == "symlink":
        target.unlink()
        try:
            target.symlink_to(tmp_path / "missing.yaml")
        except OSError as error:
            pytest.skip(f"Symlink creation unavailable: {error}")
    else:
        collect, read_text = tf.collect_local_projects, Path.read_text

        def changed(**kwargs):
            if damage == "denied":
                def denied(path, *args, **kw):
                    if path == target:
                        raise PermissionError("fixture registry denied after validation")
                    return read_text(path, *args, **kw)
                monkeypatch.setattr(Path, "read_text", denied)
            else:
                text = "[" if damage == "yaml" else f"{target.stem}:\n  demo: invalid\n"
                target.write_text(text, encoding="utf-8")
            return collect(**kwargs)

        monkeypatch.setattr(tf, "collect_local_projects", changed)
    assert profiles.main(["demo"]) == 1
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "blocked"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in outputs} == before


def test_invalid_repo_entry_returns_blocked_json(monkeypatch, tmp_path, capsys):
    registry = tmp_path / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir()
    registry.write_text("repos:\n  demo: invalid\n", encoding="utf-8")
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: tmp_path)
    assert profiles.main(["demo", "--machine", "host"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


@pytest.mark.parametrize("relative", [".agent-worktrees/machines.yaml", "machines.yaml"])
@pytest.mark.parametrize("content", ["machines: [", "machines: []", "", None])
def test_bad_resolved_roster_preserves_all_existing_outputs(monkeypatch, tmp_path, relative, content, capsys):
    home, repo = tmp_path / "home", tmp_path / "repo"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".demo").mkdir()
    (home / ".demo" / "config.yaml").write_text("machine: host\n", encoding="utf-8")
    (home / ".agent-worktrees" / "projects.yaml").write_text(
        json.dumps({"projects": {"demo": {"anchor": str(repo)}}}), encoding="utf-8",
    )
    (home / ".agent-worktrees" / "repos.yaml").write_text('{"repos": {}}', encoding="utf-8")
    roster = repo / relative
    roster.parent.mkdir(parents=True)
    roster.write_text(content if content is not None else "machines:\n  host: {}\n", encoding="utf-8")
    if content is None:
        read_text = Path.read_text

        def denied(path, *args, **kwargs):
            if path == roster:
                raise PermissionError("fixture roster denied")
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    if relative.startswith(".agent-worktrees"):
        (repo / "machines.yaml").write_text("machines:\n  host: {}\n", encoding="utf-8")
    monkeypatch.setattr(profiles.platform, "system", lambda: "Windows")
    monkeypatch.setattr(profiles.harness_state, "home", lambda: home)
    fragment_root, state = tmp_path / "fragments", tmp_path / "state"
    (fragment_root / "AgentWorktrees").mkdir(parents=True)
    state.mkdir()
    guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    outputs = {
        fragment_root / "AgentWorktrees" / "agent-worktrees.json":
            {"profiles": [{"guid": guid, "name": "Existing", "commandline": "ssh example"}]},
        state / "state.json": {"generatedProfiles": [guid]},
        state / "settings.json": {"profiles": {"list": [{"guid": guid, "source": "AgentWorktrees"}]}},
    }
    for path, data in outputs.items():
        path.write_text(json.dumps(data), encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in outputs}
    monkeypatch.setattr(tf, "_wt_fragments_dir", lambda: fragment_root)
    monkeypatch.setattr(tf, "_wt_local_state_dir", lambda: state)
    assert profiles.main(["demo"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in outputs} == before
    assert profiles.harness_state.project_roster(str(repo)) == ()


def test_unreadable_roster_blocks_publication_but_not_readonly_views(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    roster = repo / ".agent-worktrees" / "machines.yaml"
    roster.parent.mkdir(parents=True)
    roster.write_text("machines:\n  host: {}\n", encoding="utf-8")
    read_text = Path.read_text

    def denied(path, *args, **kwargs):
        if path == roster:
            raise PermissionError("fixture roster denied")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert profiles.harness_state.project_roster(str(repo)) == ()
    with pytest.raises(ValueError, match="Cannot read machine roster"):
        profiles.harness_state.project_roster(str(repo), strict=True)


def test_broken_declared_roster_does_not_fall_back_to_legacy(tmp_path):
    repo = tmp_path / "repo"
    roster = repo / ".agent-worktrees" / "machines.yaml"
    roster.parent.mkdir(parents=True)
    (repo / "machines.yaml").write_text("machines:\n  host: {}\n", encoding="utf-8")
    try:
        roster.symlink_to(tmp_path / "missing.yaml")
    except OSError as error:
        pytest.skip(f"Symlink creation unavailable: {error}")
    with pytest.raises(ValueError, match="Cannot read machine roster"):
        profiles.harness_state.project_roster(str(repo), strict=True)


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
