from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
PROVIDER = PLUGIN / "scripts" / "companion-provider.py"
SERVICE = PLUGIN / "scripts" / "companion-service.py"
REGISTER = PLUGIN / "scripts" / "register-dispatch-companion.py"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(path: Path, machine: str | None) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    policy = path / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text("requires_external_state_root: false\n", encoding="utf-8")
    if machine is not None:
        config = path / ".agent-index" / "config.yaml"
        config.parent.mkdir()
        config.write_text(
            "indexers:\n"
            f"  - machine: {machine}\n"
            f"    ssh: {machine}\n"
            "corpus:\n"
            "  sources:\n"
            "    - name: git:example\n"
            "      repo: example\n",
            encoding="utf-8",
        )
    return path


def _platform_key() -> str:
    if os.name == "nt":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _registry(home: Path, project: str, repo: Path) -> None:
    registry = home / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir(parents=True)
    platform_key = _platform_key()
    registry.write_text(
        f"repos:\n  {project}:\n    {platform_key}: {json.dumps(str(repo))}\n",
        encoding="utf-8",
    )


def _request(project: str, machine: str = "primary") -> dict:
    return {
        "schema_version": 1,
        "activation_scopes": ["global", f"project:{project}"],
        "machine": machine,
    }


def test_provider_activates_only_the_configured_host(tmp_path: Path, monkeypatch) -> None:
    module = _module(PROVIDER, "companion_provider_host")
    home = tmp_path / "home"
    repo = _repo(tmp_path / "repo", "primary")
    _registry(home, "harness", repo)
    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module, "_supports_companion_mode", lambda _env: True)

    environment = module._active_environment(_request("harness"))

    assert environment == {
        "AGENT_INDEX_EFFECTIVE_CONFIG": str((repo / ".agent-index" / "config.yaml").resolve()),
        "AGENT_INDEX_MACHINE": "primary",
        "AGENT_INDEX_NO_SELFPROVISION": "1",
        "AGENT_INDEX_REPO": str(repo.resolve()),
    }


def test_provider_is_inactive_for_client_or_missing_config(tmp_path: Path, monkeypatch) -> None:
    module = _module(PROVIDER, "companion_provider_inactive")
    home = tmp_path / "home"
    repo = _repo(tmp_path / "repo", "primary")
    empty = _repo(tmp_path / "empty", None)
    _registry(home, "harness", repo)
    registry = home / ".agent-worktrees" / "repos.yaml"
    platform_key = _platform_key()
    registry.write_text(
        registry.read_text(encoding="utf-8")
        + "  empty:\n"
        + f"    {platform_key}: {json.dumps(str(empty))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module, "_supports_companion_mode", lambda _env: True)

    assert module._active_environment(_request("harness", "client")) is None
    assert module._active_environment(_request("empty")) is None


def test_malformed_local_config_blocks_host_activation(tmp_path: Path, monkeypatch) -> None:
    module = _module(PROVIDER, "companion_provider_invalid")
    home = tmp_path / "home"
    repo = _repo(tmp_path / "repo", "primary")
    (repo / ".agent-index" / "config.yaml").write_text(
        "indexers: [\n",
        encoding="utf-8",
    )
    _registry(home, "harness", repo)
    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module, "_supports_companion_mode", lambda _env: True)

    assert module._active_environment(_request("harness")) is None


def test_provider_reports_registry_uncertainty_as_indeterminate(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module(PROVIDER, "companion_provider_indeterminate")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path / "missing-home")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_request("harness"))))

    assert module.main() == 1
    assert "indeterminate" in capsys.readouterr().err


def test_provider_reports_effective_config_uncertainty_as_indeterminate(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module(PROVIDER, "companion_provider_resolution_uncertain")
    home = tmp_path / "home"
    repo = _repo(tmp_path / "repo", None)
    _registry(home, "harness", repo)
    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(
        module,
        "resolve",
        lambda _root: {
            "opted_in": False,
            "reason": "external-state-root-unavailable",
        },
    )

    try:
        module._active_environment(_request("harness"))
    except RuntimeError as exc:
        assert "uncertain" in str(exc)
    else:
        raise AssertionError("unavailable effective config must be indeterminate")


def test_provider_keeps_namespaced_installation_inactive(tmp_path: Path, monkeypatch) -> None:
    module = _module(PROVIDER, "companion_provider_namespaced")
    home = tmp_path / "home"
    repo = _repo(tmp_path / "repo", "primary")
    _registry(home, "harness", repo)
    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module, "_supports_companion_mode", lambda _env: False)

    assert module._active_environment(_request("harness")) is None


def _fake_gate(path: Path) -> None:
    path.write_text(
        "import json, os, pathlib, sys\n"
        "action = sys.argv[1]\n"
        "marker = os.environ.get('COMPANION_TEST_MARKER')\n"
        "if marker:\n"
        "    pathlib.Path(marker).write_text(\n"
        "        action + ':' + os.environ.get('AGENT_INDEX_NO_SELFPROVISION', ''),\n"
        "        encoding='utf-8',\n"
        "    )\n"
        "if action == 'status':\n"
        "    print(json.dumps({'running': os.environ.get('RUNNING') == '1', "
        "'state': 'ready'}))\n"
        "elif action == '__dispatch-companion-mode':\n"
        "    print(json.dumps({'schema_version': 1, 'supported': True, "
        "'mode': 'legacy'}))\n"
        "elif action == 'stop':\n"
        "    print(json.dumps({'stopped': False, 'reason': 'not-running'}))\n",
        encoding="utf-8",
    )


def test_service_adapter_forces_no_self_provision(tmp_path: Path, monkeypatch) -> None:
    module = _module(SERVICE, "companion_service_start")
    gate = tmp_path / "gate.py"
    marker = tmp_path / "called"
    _fake_gate(gate)
    monkeypatch.setattr(
        module, "_runtime_gate", lambda action: [sys.executable, str(gate), action]
    )
    monkeypatch.setenv("COMPANION_TEST_MARKER", str(marker))
    monkeypatch.delenv("AGENT_INDEX_NO_SELFPROVISION", raising=False)
    monkeypatch.setattr(module, "_companion_mode_supported", lambda: True)

    assert module._start() == 0
    assert marker.read_text(encoding="utf-8") == "start:1"


def test_service_adapter_scrubs_inherited_runtime_authority(monkeypatch) -> None:
    module = _module(SERVICE, "companion_service_environment")
    monkeypatch.setenv("AGENT_INDEX_HOME", "wrong-home")
    monkeypatch.setenv("AGENT_INDEX_STATE_DIR", "wrong-state")
    monkeypatch.setenv("AGENT_INDEX_CONFIG_DATA_B64", "forwarded")
    monkeypatch.setenv("AGENT_INDEX_EFFECTIVE_CONFIG", "approved-config")
    monkeypatch.setenv("AGENT_INDEX_REPO", "approved-repo")
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "approved-machine")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "wrong-context")
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    monkeypatch.setenv("UV_INDEX_URL", "https://example.invalid")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://example.invalid")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MODE", "inprocess")

    environment = module._runtime_environment()

    assert "AGENT_INDEX_HOME" not in environment
    assert "AGENT_INDEX_STATE_DIR" not in environment
    assert "AGENT_INDEX_CONFIG_DATA_B64" not in environment
    assert "COPILOT_EXTENSIONS_CONTEXT" not in environment
    assert environment["AGENT_INDEX_EFFECTIVE_CONFIG"] == "approved-config"
    assert environment["AGENT_INDEX_REPO"] == "approved-repo"
    assert environment["AGENT_INDEX_MACHINE"] == "approved-machine"
    assert environment["AGENT_INDEX_NO_SELFPROVISION"] == "1"
    assert environment["AGENT_INDEX_MANAGED_PYTHON"] == sys.executable
    assert environment["AGENT_INDEX_ENGINE_MODE"] == "external"
    assert environment["AGENT_INDEX_INDEX_ENSURE_ENGINES"] == "0"
    assert "UV_INDEX_URL" not in environment
    assert "PIP_EXTRA_INDEX_URL" not in environment
    assert "PYTHONPATH" not in environment


def test_service_adapter_does_not_stop_unsupported_installation(
    monkeypatch,
) -> None:
    module = _module(SERVICE, "companion_service_mode")
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    calls: list[str] = []

    def run(action: str, *, capture: bool = False):
        calls.append(action)
        return subprocess.CompletedProcess(
            [action],
            0,
            stdout=json.dumps({"schema_version": 1, "supported": False, "mode": "namespaced"}),
            stderr="",
        )

    monkeypatch.setattr(module, "_run", run)
    monkeypatch.setattr(
        module, "installation_mode",
        lambda: {"schema_version": 1, "supported": False, "mode": "namespaced"},
    )

    assert module._start() == 1
    assert calls == []
    calls.clear()
    assert module._stop() == 1
    assert calls == []


def test_service_health_translates_confirmed_status(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _module(SERVICE, "companion_service_health")
    gate = tmp_path / "gate.py"
    _fake_gate(gate)
    monkeypatch.setattr(
        module, "_runtime_gate", lambda action: [sys.executable, str(gate), action]
    )
    monkeypatch.setenv("RUNNING", "1")

    assert module._health() == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "healthy": True,
        "detail": "agent-index service is reachable",
    }


def test_session_hook_publishes_attributed_registrar_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module(REGISTER, "register_dispatch_companion")
    dropins = tmp_path / "registrar.d"
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", str(dropins))

    assert module.main() == 0
    manifest = json.loads(
        (dropins / "agent-index-copilot-extensions.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema_version": 1,
        "plugin": "agent-index@copilot-extensions",
        "plugin_root": str(PLUGIN.resolve()),
        "registrar": "references/agent-dispatch/registrar",
    }
    assert module.main() == 0


def test_python_registrar_empty_override_uses_default_home(tmp_path: Path, monkeypatch) -> None:
    module = _module(REGISTER, "register_dispatch_companion_default")
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", "")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)

    assert module.main() == 0
    assert (
        tmp_path / ".agent-dispatch" / "registrar.d" / "agent-index-copilot-extensions.json"
    ).is_file()


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_registrar_hook_wrapper_publishes_candidate(tmp_path: Path, shell: str) -> None:
    environment = {
        **os.environ,
        "AGENT_DISPATCH_REGISTRAR_DROPINS_DIR": str(tmp_path / "registrar.d"),
    }
    if shell == "bash":
        executable = shutil.which("bash")
        if executable is None or os.name == "nt":
            pytest.skip("POSIX registrar hook test")
        command = [
            executable,
            str(PLUGIN / "scripts" / "register-dispatch-companion.sh"),
        ]
    else:
        executable = shutil.which("powershell.exe") or shutil.which("pwsh")
        if executable is None:
            pytest.skip("PowerShell registrar hook test")
        command = [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PLUGIN / "scripts" / "register-dispatch-companion.ps1"),
        ]

    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(
        (tmp_path / "registrar.d" / "agent-index-copilot-extensions.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["plugin"] == "agent-index@copilot-extensions"
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()


def test_companion_declaration_and_hook_remain_non_provisioning() -> None:
    declaration = json.loads(
        (
            PLUGIN / "references" / "agent-dispatch" / "registrar" / "agent-index-service.json"
        ).read_text(encoding="utf-8")
    )
    assert declaration["kind"] == "plugin-companion"
    assert declaration["spec"]["command"] == [
        "scripts/companion-service.py",
        "start",
    ]
    assert "ensure-service" not in json.dumps(declaration)
    assert "install" not in json.dumps(declaration)
    runtime, = declaration["spec"]["managed_runtime"]["runtimes"]
    assert runtime["python_env"] == "AGENT_INDEX_MANAGED_PYTHON"
    assert runtime["profile"] == "host"
    assert runtime["projects"][-1] == {"path": ".", "extras": ["store"]}
    assert "engine" not in json.dumps(runtime)

    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["sessionStart"]
    assert any("register-dispatch-companion.sh" in hook["bash"] for hook in session_start)
    for hook in session_start:
        assert "ensure-service" not in hook["bash"]
        assert "ensure-service" not in hook["powershell"]
    powershell_writer = (PLUGIN / "scripts" / "register-dispatch-companion.ps1").read_text(
        encoding="utf-8"
    )
    assert "Get-Command python" not in powershell_writer


@pytest.mark.parametrize("action", ["start", "stop", "health"])
def test_missing_dispatch_selection_never_launches_or_provisions(
    monkeypatch, capsys, action
) -> None:
    module = _module(SERVICE, "companion_service_missing_dispatch")
    monkeypatch.delenv("AGENT_INDEX_MANAGED_PYTHON", raising=False)
    monkeypatch.setattr(module, "_companion_mode_supported", lambda: True)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *_a, **_k: pytest.fail("process launched")
    )
    monkeypatch.setattr(sys, "argv", [str(SERVICE), action])
    assert module.main() == 1
    assert "dispatch-selected" in capsys.readouterr().err


def test_selected_interpreter_is_the_only_lifecycle_target(monkeypatch) -> None:
    module = _module(SERVICE, "companion_service_selected")
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    assert module._runtime_gate("start") == [
        sys.executable, "-I", "-B", "-m", "agent_index", "__managed-start",
    ]
    for action in ("status", "stop"):
        assert module._runtime_gate(action)[-1] == action
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", "python")
    with pytest.raises(RuntimeError, match="dispatch-selected"):
        module._runtime_gate("start")


@pytest.mark.parametrize("scopes", [[], ["global"], ["session:example"], ["project:"]])
def test_non_project_activation_scopes_are_inert(monkeypatch, scopes) -> None:
    module = _module(PROVIDER, "companion_provider_scope")
    monkeypatch.setattr(
        module, "_supports_companion_mode",
        lambda _env: pytest.fail("installation inspected without a host"),
    )
    assert module._active_environment({"machine": "primary", "activation_scopes": scopes}) is None


def test_companion_mode_uses_attributed_payload_without_a_runtime(
    tmp_path, monkeypatch
) -> None:
    module = _module(PLUGIN / "scripts" / "companion_context.py", "companion_context_test")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "sibling-install.json")
    monkeypatch.setenv("AGENT_INDEX_PAYLOAD_ROOT", "unattributed-payload")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", "unattributed-origin")
    mode = module.installation_mode()
    assert mode["supported"] is True
    assert mode["mode"] == "legacy"
    assert list(tmp_path.iterdir()) == []
    policy = tmp_path / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir()
    policy.write_text(
        json.dumps({"version": 1, "installationMode": {"enabled": True}}),
        encoding="utf-8",
    )
    assert module.installation_mode()["supported"] is False
    assert not (tmp_path / ".agent-index").exists()
    policy.write_text("{", encoding="utf-8")
    assert module.installation_mode()["supported"] is False


def test_unattributed_payload_cannot_fall_back_to_legacy_host(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "unattributed" / "agent-index"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("plugin.json", "payload-invocation.json"):
        shutil.copy2(PLUGIN / name, payload / name)
    shutil.copytree(PLUGIN / "scripts" / "installation-context", scripts / "installation-context")
    module = _module(PLUGIN / "scripts" / "companion_context.py", "companion_unattributed")
    monkeypatch.setattr(module, "__file__", str(scripts / "companion_context.py"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    mode = module.installation_mode()
    assert mode["supported"] is False
    assert mode["status"] == "provenance-blocked"
    assert not (tmp_path / ".agent-index").exists()


def _assert_no_owned_windows(pids: set[int]) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    windows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _data):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            windows.append(pid.value)
        return True

    assert user32.EnumWindows(visit, 0)
    foreground_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(foreground_pid))
    assert windows == []
    assert foreground_pid.value not in pids


def test_managed_adapter_runs_real_service_without_plugin_or_engine_provisioning(
    tmp_path, monkeypatch
) -> None:
    repo = _repo(tmp_path / "repo", "primary")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    module = _module(SERVICE, "companion_service_real")
    environment = {
        **module._runtime_environment(),
        "AGENT_INDEX_MANAGED_PYTHON": sys.executable,
        "AGENT_INDEX_REPO": str(repo),
        "AGENT_INDEX_EFFECTIVE_CONFIG": str(repo / ".agent-index" / "config.yaml"),
        "AGENT_INDEX_MACHINE": "primary",
    }
    launcher = Path(sys.executable)
    if os.name == "nt":
        launcher = launcher.with_name("pythonw.exe")
        assert launcher.is_file()
    log = tmp_path / "service.log"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [str(launcher), str(SERVICE), "start"],
            cwd=repo, env=environment, stdout=output, stderr=output,
            creationflags=flags,
        )
        try:
            deadline = time.monotonic() + 15
            healthy = False
            while time.monotonic() < deadline and process.poll() is None:
                result = subprocess.run(
                    [sys.executable, str(SERVICE), "health"],
                    cwd=repo, env=environment, capture_output=True, text=True,
                    creationflags=flags, timeout=5,
                )
                if result.returncode == 0 and json.loads(result.stdout)["healthy"]:
                    healthy = True
                    break
                time.sleep(0.1)
            assert healthy, log.read_text(encoding="utf-8")
            endpoint = json.loads(
                (home / ".agent-index" / "run" / "endpoint.json").read_text(encoding="utf-8")
            )
            for _ in range(2):
                _assert_no_owned_windows({process.pid, endpoint["pid"]})
                result = subprocess.run(
                    [sys.executable, str(SERVICE), "health"],
                    cwd=repo, env=environment, capture_output=True, text=True,
                    creationflags=flags, timeout=5,
                )
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout)["healthy"] is True
                _assert_no_owned_windows({process.pid, endpoint["pid"]})
                time.sleep(0.1)
            assert not (home / ".agent-index" / "versions").exists()
            assert not (home / ".agent-index" / "engine").exists()
            assert not (home / ".agent-index" / "current-version").exists()
        finally:
            stopped = subprocess.run(
                [sys.executable, str(SERVICE), "stop"],
                cwd=repo, env=environment, capture_output=True, text=True,
                creationflags=flags, timeout=15,
            )
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            assert stopped.returncode == 0, stopped.stderr
