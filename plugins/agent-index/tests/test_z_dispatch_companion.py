from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
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


def _registry(home: Path, project: str, repo: Path) -> None:
    registry = home / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir(parents=True)
    platform_key = "windows" if os.name == "nt" else "linux"
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
    platform_key = "windows" if os.name == "nt" else "linux"
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

    environment = module._runtime_environment()

    assert "AGENT_INDEX_HOME" not in environment
    assert "AGENT_INDEX_STATE_DIR" not in environment
    assert "AGENT_INDEX_CONFIG_DATA_B64" not in environment
    assert "COPILOT_EXTENSIONS_CONTEXT" not in environment
    assert environment["AGENT_INDEX_EFFECTIVE_CONFIG"] == "approved-config"
    assert environment["AGENT_INDEX_REPO"] == "approved-repo"
    assert environment["AGENT_INDEX_MACHINE"] == "approved-machine"
    assert environment["AGENT_INDEX_NO_SELFPROVISION"] == "1"


def test_service_adapter_does_not_stop_unsupported_installation(
    monkeypatch,
) -> None:
    module = _module(SERVICE, "companion_service_mode")
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

    assert module._start() == 1
    assert calls == ["__dispatch-companion-mode"]
    calls.clear()
    assert module._stop() == 1
    assert calls == ["__dispatch-companion-mode"]


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
