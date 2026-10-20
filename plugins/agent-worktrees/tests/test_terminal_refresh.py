"""Profile refresh crosses the narrow Manager boundary, never an installer."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import manager_launch_cli, picker_profiles_cli as m

PLUGIN = Path(__file__).resolve().parents[1]
SUCCESS = {
    "schema": "copilot-extensions.terminal-profile-refresh",
    "version": 1,
    "status": "verified_current",
}


@pytest.fixture
def manager(monkeypatch):
    selected = SimpleNamespace(command=("manager",))
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        lambda **k: {"worktree-manager": selected},
    )
    monkeypatch.setattr(m.cfg, "project_name", lambda: "demo")
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: SimpleNamespace(machine="host"))
    return selected


def test_missing_manager_is_explicit(monkeypatch, capsys):
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests", lambda **k: {},
    )
    assert m._refresh_terminal_profiles() is False
    assert "Worktree Manager is unavailable" in capsys.readouterr().out


def test_refresh_uses_one_narrow_call_without_installer(manager, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr="")

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m._refresh_terminal_profiles() is True
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["manager", "refresh-profiles", "demo", "--machine", "host"]
    assert not any("install." in value or value == "--version" for value in argv)
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["timeout"] == 60


def test_explicit_registration_target_does_not_require_active_context(manager, monkeypatch):
    def no_context():
        raise AssertionError("active project lookup must not run for an explicit target")

    monkeypatch.setattr(m.cfg, "project_name", no_context)
    captured = []
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda argv, **k: captured.append(argv) or SimpleNamespace(
            returncode=0, stdout=json.dumps(SUCCESS), stderr="",
        ),
    )
    assert m._refresh_terminal_profiles("registered") is True
    assert captured == [["manager", "refresh-profiles", "registered", "--machine", "host"]]


def test_register_without_active_project_refreshes_the_registered_target(monkeypatch, tmp_path):
    from agent_worktrees import installation_cli, installer, repos

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        lambda **k: {"worktree-manager": SimpleNamespace(command=("manager",))},
    )
    monkeypatch.setattr(m.cfg, "_ACTIVE_PROJECT", None)
    monkeypatch.setattr(m.cfg, "project_dir", lambda name: tmp_path / "config")
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(m.cfg, "machines_yaml_path", lambda path: repo / "missing.yaml")
    monkeypatch.setattr(
        m.cfg, "load_config",
        lambda *a, **k: SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(enabled=False))),
    )
    overrides = {
        "_prepare_namespaced_project_state": lambda *a, **k: True,
        "_write_global_config": lambda *a, **k: None,
        "_write_config": lambda *a, **k: None,
        "_cleanup_stale_instructions": lambda *a, **k: None,
        "_ensure_ado_pr_cli": lambda *a, **k: None,
    }
    monkeypatch.setattr(installation_cli, "_core_helper", lambda name, local: overrides.get(name, local))
    monkeypatch.setattr(repos, "find_repo", lambda *a, **k: None)
    monkeypatch.setattr(repos, "add_repo", lambda *a, **k: None)
    monkeypatch.setattr(installer, "register_project", lambda *a, **k: None)
    monkeypatch.setattr(installer, "deploy_binstubs", lambda *a, **k: True)

    @contextmanager
    def registration(*args, **kwargs):
        yield SimpleNamespace(commit=lambda: None)

    monkeypatch.setattr(installer, "project_binstub_registration", registration)
    captured = []

    def run(argv, **kwargs):
        if argv[0] == "git":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        captured.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr="")

    monkeypatch.setattr(m.subprocess, "run", run)
    args = SimpleNamespace(
        project_name="registered", repo_dir=str(repo), machine="example-host",
        default_branch="main", force=False, agent=True,
    )
    assert installation_cli.cmd_register(args) == 0
    assert m.cfg.active_project() is None
    assert captured == [["manager", "refresh-profiles", "registered", "--machine", "example-host"]]


@pytest.mark.parametrize("source", ["global", "detection"])
def test_resolved_machine_without_project_machine_setting(monkeypatch, tmp_path, source):
    config_dir = tmp_path / ".registered"
    config_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (config_dir / "config.yaml").write_text(
        json.dumps({"repos": {"registered": {"anchor": str(repo)}}}), encoding="utf-8",
    )
    global_config = tmp_path / "global.yaml"
    if source == "global":
        global_config.write_text("machine: global-host\n", encoding="utf-8")
    monkeypatch.setattr(m.cfg, "global_config_path", lambda: global_config)
    monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr(m.cfg, "detect_machine", lambda *a: "detected-host")
    monkeypatch.setattr(m.cfg, "_ACTIVE_PROJECT", None)
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        lambda **k: {"worktree-manager": SimpleNamespace(command=("manager",))},
    )
    captured = []

    def run(argv, **kwargs):
        if argv[0] != "manager":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        captured.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr="")

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m._refresh_terminal_profiles("registered")
    expected = "global-host" if source == "global" else "detected-host"
    assert captured == [["manager", "refresh-profiles", "registered", "--machine", expected]]
    assert m.cfg.active_project() is None


@pytest.mark.parametrize("response", [
    "old manager help text", "{}", "[]",
    json.dumps({**SUCCESS, "status": "blocked"}),
    json.dumps({**SUCCESS, "version": 2}),
])
def test_zero_exit_without_verified_protocol_is_failure(manager, monkeypatch, response):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=response, stderr=""),
    )
    assert m._refresh_terminal_profiles() is False


def test_nonzero_exit_is_failure_even_with_success_body(manager, monkeypatch):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=json.dumps(SUCCESS), stderr=""),
    )
    assert m._refresh_terminal_profiles() is False


@pytest.mark.parametrize("response", ["", "old manager help", "[]", "{}"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_invalid_profile_response_preserves_startup_stderr(
    manager, monkeypatch, capsys, response, exit_code,
):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(
            returncode=exit_code, stdout=response,
            stderr="  dependency resolution failed under configured policy\n",
        ),
    )
    assert m._refresh_terminal_profiles() is False
    warning = capsys.readouterr().out
    assert f"Manager exited {exit_code}" in warning
    assert "stderr: dependency resolution failed under configured policy" in warning
    assert "profiles refreshed" not in warning


def test_profile_failure_retains_protocol_error_and_bounded_redacted_stderr(
    manager, monkeypatch, capsys,
):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(
            returncode=2,
            stdout=json.dumps({**SUCCESS, "status": "blocked", "error": "profile write denied"}),
            stderr="https://fixture-user:fixture-password@example.invalid/ " + "x" * 1000,
        ),
    )
    assert m._refresh_terminal_profiles() is False
    warning = capsys.readouterr().out
    assert "Manager exited 2: profile write denied; stderr:" in warning
    assert "example.invalid" in warning
    assert "fixture-user" not in warning and "fixture-password" not in warning
    assert len(warning.split("Could not refresh Windows Terminal profiles: ", 1)[1].strip()) <= 400


def test_real_manager_startup_failure_preserves_diagnostics(manager, capsys):
    manager.command = (
        sys.executable, "-c",
        "import sys; print('startup dependency refused', file=sys.stderr); sys.exit(7)",
    )
    assert m._refresh_terminal_profiles() is False
    warning = capsys.readouterr().out
    assert "Manager exited 7" in warning
    assert "invalid profile JSON" in warning
    assert "stderr: startup dependency refused" in warning


@pytest.mark.parametrize("error", [
    OSError("cannot launch manager"), subprocess.TimeoutExpired("manager", 60),
])
def test_spawn_and_timeout_errors_are_visible(manager, monkeypatch, capsys, error):
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m._refresh_terminal_profiles() is False
    assert "Could not refresh Windows Terminal profiles" in capsys.readouterr().out


def test_real_profile_process_protocol(manager):
    manager.command = (
        sys.executable, "-c",
        "import json,sys; assert sys.argv[1:] == ['refresh-profiles','demo','--machine','host']; "
        f"print(json.dumps({SUCCESS!r}))",
    )
    assert m._refresh_terminal_profiles() is True


@pytest.mark.parametrize("override", [False, True])
def test_cell_profile_refresh_uses_machine_manager_registration(monkeypatch, tmp_path, override):
    from agent_worktrees import control_plane_providers as providers
    from agent_worktrees import registry_paths

    home = tmp_path / "home"
    registered = (tmp_path / "explicit-providers" if override else
                  home / ".agent-worktrees" / "control-plane-providers.d")
    registered.mkdir(parents=True)
    (registered / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager",
        "command": ["registered-manager"], "minimum_version": "0.1.0-dev21",
    }), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "plugin-sandbox"))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "cell" / "install.json"))
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    if override:
        monkeypatch.setenv(providers._CONTROL_PLANE_PROVIDERS_DIR_ENV, str(registered))
    else:
        monkeypatch.delenv(providers._CONTROL_PLANE_PROVIDERS_DIR_ENV, raising=False)
    monkeypatch.setattr(
        m.cfg, "install_dir",
        lambda: pytest.fail("profile Manager discovery must not inspect the plugin cell"),
    )
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        providers._discover_control_plane_provider_manifests,
    )
    calls = []
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda argv, **k: calls.append(argv) or SimpleNamespace(
            returncode=0, stdout=json.dumps(SUCCESS), stderr="",
        ),
    )
    assert m._refresh_terminal_profiles("demo", machine="host") is True
    assert calls == [["registered-manager", "refresh-profiles", "demo", "--machine", "host"]]


@pytest.mark.parametrize("native", [False, True])
def test_cell_refresh_transports_validated_roots_without_changing_global_home(
    manager, monkeypatch, tmp_path, native,
):
    from agent_worktrees import project_state
    from test_registry_paths import _payload, _select, _stamp

    if native and os.name != "nt":
        pytest.skip("Windows Terminal and Shell COM integration")
    payload = _payload(tmp_path, "payload")
    context, registry = _stamp(
        tmp_path / "durable", payload, marketplace="example",
        repository="Example-Org/Example-Marketplace",
    )
    _select(monkeypatch, context, payload)
    (registry / "projects.yaml").write_text("projects:\n  demo: {}\n  peer: {}\n",
                                            encoding="utf-8")
    (registry / "repos.yaml").write_text(json.dumps({"repos": {
        name: {"class": "worktree", "remote": f"https://github.com/example/{name}.git"}
        for name in ("demo", "peer")
    }}), encoding="utf-8")
    roots = {name: project_state.ensure_project_state(name) for name in ("demo", "peer")}
    if native:
        home = tmp_path / "home"
        legacy = home / ".agent-worktrees"
        legacy.mkdir(parents=True)
        (legacy / "projects.yaml").write_text("projects:\n  legacy: {}\n", encoding="utf-8")
        (home / ".demo").mkdir()
        (home / ".demo" / "config.yaml").write_text("terminal_profiles: []\n", encoding="utf-8")
        for root in roots.values():
            (root / "config.yaml").write_text(
                "terminal_profiles:\n- {machine: host, env: Win, kind: shell}\n",
                encoding="utf-8",
            )
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))
        manager_src = PLUGIN.parents[1] / "worktree-manager" / "src"
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join([
            str(manager_src), os.environ.get("PYTHONPATH", ""),
        ]))
        manager.command = (sys.executable, "-m", "worktree_manager")
        initial = None
        for attempt in range(2):
            assert m._refresh_terminal_profiles("demo", machine="host")
            fragment = (home / "local" / "Microsoft" / "Windows Terminal" / "Fragments"
                        / "AgentWorktrees" / "agent-worktrees.json")
            names = {row["name"] for row in json.loads(fragment.read_text("utf-8"))["profiles"]}
            assert names == {"Demo", "Peer", "host"}
            assert (home / ".local" / "bin" / "Peer.lnk").is_file()
            if attempt:
                assert fragment.stat().st_mtime_ns == initial
            else:
                initial = fragment.stat().st_mtime_ns
        return
    home_before = os.environ.get("USERPROFILE")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert os.environ.get("USERPROFILE") == home_before
        return SimpleNamespace(returncode=0, stdout=json.dumps(SUCCESS), stderr="")

    monkeypatch.setattr(m.subprocess, "run", run)
    assert m._refresh_terminal_profiles("demo", machine="host")
    argv, kwargs = calls[0]
    assert argv[-1] == "--input-roots-stdin"
    value = json.loads(kwargs["input"])
    assert value["registryRoot"] == str(registry)
    assert value["context"] == str(context)
    assert value["payloadRoot"] == str(payload)
    assert value["projectRoots"] == {name: str(root) for name, root in roots.items()}


def test_invalid_cell_context_never_launches_manager(manager, monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "missing-install.json"))
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: pytest.fail("Invalid context launched Manager"))
    assert m._refresh_terminal_profiles("demo", machine="host") is False


@pytest.mark.guard
@pytest.mark.parametrize(("defer", "expected"), [("1", 0), ("", 2)])
def test_native_powershell_installer_defers_both_reconcile_passes(defer, expected):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    function = source.split("function Reconcile-Binstubs {", 1)[1].split(
        "\n# -- Actions", 1,
    )[0]
    script = (
        "$ErrorActionPreference='Stop'; $VenvPython='Invoke-FixtureRuntime'; "
        "$PluginDir='fixture'; $global:count=0; "
        "function Test-Path { param($Path) $true }; "
        "function Write-ServiceWarn { param($Message) throw $Message }; "
        "function Invoke-FixtureRuntime { "
        "if (($args -join '|') -ne '-m|agent_worktrees|reconcile-binstubs') "
        "{ throw 'wrong runtime arguments' }; $global:count++ }; "
        "function Reconcile-Binstubs {" + function
        + "\nReconcile-Binstubs; Reconcile-Binstubs; "
        + f"if ($global:count -ne {expected}) {{ throw 'wrong reconciliation count' }}"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": defer},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.guard
@pytest.mark.parametrize(("defer", "expected"), [("1", 0), ("", 2)])
def test_native_posix_installer_defers_both_reconcile_passes(tmp_path, defer, expected):
    if os.name == "nt":
        pytest.skip("Requires native POSIX, not Git Bash compatibility")
    source = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    function = source.split("reconcile_binstubs() {", 1)[1].split("\ndeploy_binstub() {", 1)[0]
    runtime, counter = tmp_path / "runtime", tmp_path / "counter"
    runtime.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$COUNT_FILE"\n', encoding="utf-8")
    runtime.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nwarn() { echo \"$*\" >&2; exit 1; }\n"
         "reconcile_binstubs() {" + function + "\nreconcile_binstubs\nreconcile_binstubs\n"],
        env={**os.environ, "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": defer,
             "VENV_PYTHON": str(runtime), "PLUGIN_DIR": "fixture", "COUNT_FILE": str(counter)},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []
    assert lines == ["-m agent_worktrees reconcile-binstubs"] * expected


def test_installers_keep_single_registry_and_fragment_owners():
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "register-project-entry" in ps and "register-project-entry" in sh
    assert "function Write-ProjectsRegistry" not in ps
    assert "$entry['anchor']" not in ps and "entry['anchor']" not in sh
    assert "function Build-TerminalFragment" not in ps


def test_native_installer_defers_profile_side_effects_to_update():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    function = source.split("function Deploy-Shortcuts {", 1)[1].split(
        "\nfunction Deploy-CopilotPlugin {", 1,
    )[0]
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; "
         "function Deploy-TerminalFragmentViaWorktreeManager { throw 'unexpected profile deploy' }; "
         "function Deploy-Shortcuts {" + function + "\nDeploy-Shortcuts -Machine example"],
        env={**os.environ, "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": "1"},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("body", "exit_code", "expected"), [
    (json.dumps(SUCCESS), 0, True),
    (json.dumps({**SUCCESS, "status": "blocked", "error": "fixture denial"}), 1, False),
    ("old manager help", 0, False),
])
def test_native_installer_compatibility_uses_verified_manager_result(body, exit_code, expected):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    function = source.split("function Deploy-Shortcuts {", 1)[1].split(
        "\nfunction Deploy-CopilotPlugin {", 1,
    )[0]
    script = (
        "$ErrorActionPreference='Stop'; $ProjectName='demo'; "
        "function Write-ServiceWarn { param($Message) }; "
        "function Write-ServiceOk { param($Message) }; "
        "function Get-UsableWorktreeManagerBin { 'Invoke-FixtureManager' }; "
        "function Invoke-FixtureManager { "
        "if (($args -join '|') -ne 'refresh-profiles|demo|--machine|example') "
        "{ throw 'wrong Manager arguments' }; "
        f"$global:LASTEXITCODE={exit_code}; '{body.replace(chr(39), chr(39) * 2)}' "
        "}; function Deploy-Shortcuts {" + function
        + "\n$result = Deploy-Shortcuts -Machine example; "
        + f"if ($result -ne ${str(expected).lower()}) {{ throw 'wrong refresh outcome' }}"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION": ""},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
