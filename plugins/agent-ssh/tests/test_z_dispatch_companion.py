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
from types import ModuleType

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
PROVIDER = PLUGIN / "scripts" / "companion-provider.py"
REGISTER = PLUGIN / "scripts" / "register-dispatch-companion.py"
COMPANION = PLUGIN / "scripts" / "dtssh-companion.ps1"
DECLARATION = (
    PLUGIN / "references" / "agent-dispatch" / "registrar" / "agent-ssh-dtssh-host.json"
)
PWSH: str = shutil.which("pwsh") or shutil.which("powershell.exe") or ""
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_host_install(
    install_root: Path,
    *,
    healthy_after_start: bool = True,
    include_backup_root: bool = True,
    watchdog_pid: int = 123,
) -> tuple[Path, Path]:
    install_root.mkdir(parents=True, exist_ok=True)
    config = install_root / "dispatch-companion.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "alias": "example-host",
                "port": 2222,
                "tunnel": None,
                "user": None,
                **(
                    {"host_key_backup_root": str(install_root / "backup")}
                    if include_backup_root
                    else {}
                ),
            }
        ),
        encoding="utf-8",
    )
    installer = install_root / "install-host.ps1"
    state = install_root / "running.txt"
    installer.write_text(
        "\n".join(
            [
                "param(",
                "  [string]$Action = 'status',",
                "  [string]$Alias,",
                "  [int]$Port,",
                "  [string]$Tunnel,",
                "  [string]$User,",
                "  [string]$HostKeyBackupRoot,",
                "  [switch]$ForegroundLauncher",
                ")",
                "$ErrorActionPreference = 'Stop'",
                "$state = Join-Path (Split-Path $PSCommandPath -Parent) 'running.txt'",
                (
                    "Add-Content -LiteralPath "
                    "(Join-Path (Split-Path $PSCommandPath -Parent) 'actions.log') -Value $Action"
                ),
                "switch ($Action) {",
                "  'stop' {",
                "    if (Test-Path -LiteralPath $state) { Remove-Item -LiteralPath $state -Force }",
                "    'stopped' | Out-Null",
                "    exit 0",
                "  }",
                "  'start' {",
                "    if (-not $ForegroundLauncher) { throw 'start requires -ForegroundLauncher' }",
                "    Set-Content -LiteralPath $state -Value 'running' -Encoding utf8NoBOM",
                "    try {",
                "      while (Test-Path -LiteralPath $state) { Start-Sleep -Milliseconds 100 }",
                "    } finally {",
                "      Remove-Item -LiteralPath $state -Force -ErrorAction SilentlyContinue",
                "    }",
                "    exit 0",
                "  }",
                "  'status' {",
                "    if (Test-Path -LiteralPath $state) {",
                (
                    "      Write-Host 'host running: 321'; "
                    "Write-Host 'sshd serving: 127.0.0.1:2222 (SSH banner OK)'; "
                    f"Write-Host 'watchdog running: {watchdog_pid}'; "
                    "Write-Host 'dispatch companion config: configured'; "
                    "Write-Host 'tunnel example: 1 host connection(s)'; "
                    "Write-Host 'durable host identity: C:\\backup'"
                    if healthy_after_start
                    else
                    "      Write-Warning 'host not running'"
                ),
                "    } else {",
                "      Write-Warning 'host not running'",
                "      Write-Warning 'watchdog not running'",
                "      Write-Host 'dispatch companion config: configured'",
                "      Write-Host 'tunnel example: 0 host connection(s)'",
                "    }",
                "    exit 0",
                "  }",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    (install_root / "dtssh-host-launcher.ps1").write_text("", encoding="utf-8")
    return config, state


def test_provider_is_inactive_without_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module(PROVIDER, "agent_ssh_companion_provider_inactive")
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    # The pwsh prerequisite is host-dependent; this test pins the config logic.
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert module._active_environment({"machine": "primary"}) is None


def test_provider_activates_for_windows_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module(PROVIDER, "agent_ssh_companion_provider_active")
    install_root = tmp_path / "agent-ssh-dtssh"
    config, _state = _write_host_install(install_root)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    # The pwsh prerequisite is host-dependent; this test pins the config logic.
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert module._active_environment({"machine": "primary"}) == {
        "AGENT_SSH_DTSSH_COMPANION_CONFIG": str(config.resolve())
    }


def test_provider_reports_malformed_config_as_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module(PROVIDER, "agent_ssh_companion_provider_bad")
    install_root = tmp_path / "agent-ssh-dtssh"
    install_root.mkdir(parents=True)
    (install_root / "dispatch-companion.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    # The pwsh prerequisite is host-dependent; this test pins the config logic.
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"schema_version": 1, "machine": "primary"})))

    assert module.main() == 1
    assert "indeterminate" in capsys.readouterr().err


def test_session_hook_publishes_attributed_registrar_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module(REGISTER, "agent_ssh_register_dispatch_companion")
    dropins = tmp_path / "registrar.d"
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", str(dropins))

    assert module.main() == 0
    manifest = json.loads(
        (dropins / "agent-ssh-copilot-extensions.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema_version": 1,
        "plugin": "agent-ssh@copilot-extensions",
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
        if not PWSH:
            pytest.skip("PowerShell registrar hook test")
        command = [
            PWSH,
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
        (tmp_path / "registrar.d" / "agent-ssh-copilot-extensions.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["plugin"] == "agent-ssh@copilot-extensions"
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()


def test_companion_declaration_and_hook_remain_non_provisioning() -> None:
    declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))

    assert declaration["kind"] == "plugin-companion"
    assert declaration["spec"]["command"] == [
        "scripts/dtssh-companion.ps1",
        "start",
    ]
    assert declaration["spec"]["stop_command"] == [
        "scripts/dtssh-companion.ps1",
        "stop",
    ]
    assert declaration["spec"]["health_probe"] == [
        "scripts/dtssh-companion.ps1",
        "health",
    ]
    assert declaration["spec"]["config_provider"] == [
        "scripts/companion-provider.py"
    ]
    assert "install" not in json.dumps(declaration)

    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["sessionStart"]
    assert any("register-dispatch-companion.sh" in hook["bash"] for hook in session_start)
    for hook in session_start:
        if "register-dispatch-companion" in hook.get("bash", ""):
            assert hook["timeoutSec"] == 10


@pytest.mark.skipif(not PWSH, reason="PowerShell is unavailable")
def test_companion_restores_presence_without_startup_shortcut(tmp_path: Path) -> None:
    install_root = tmp_path / "local" / "agent-ssh-dtssh"
    config, state = _write_host_install(install_root)
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "local"),
        "AGENT_SSH_DTSSH_COMPANION_CONFIG": str(config),
    }

    unhealthy = subprocess.run(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "health"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert unhealthy.returncode == 0, unhealthy.stderr
    assert json.loads(unhealthy.stdout)["healthy"] is False

    process = subprocess.Popen(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not state.exists():
            time.sleep(0.1)
        assert state.exists(), "companion did not start the foreground launcher"

        healthy = subprocess.run(
            [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "health"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        assert healthy.returncode == 0, healthy.stderr
        assert json.loads(healthy.stdout)["healthy"] is True

        stopped = subprocess.run(
            [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "stop"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        assert stopped.returncode == 0, stopped.stderr
        process.wait(timeout=10)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.skipif(not PWSH, reason="PowerShell is unavailable")
def test_companion_start_adopts_healthy_host_without_restart(tmp_path: Path) -> None:
    watchdog = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        creationflags=NO_WINDOW,
    )
    install_root = tmp_path / "local" / "agent-ssh-dtssh"
    config, state = _write_host_install(install_root, watchdog_pid=watchdog.pid)
    state.write_text("running", encoding="utf-8")
    actions = install_root / "actions.log"
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "local"),
        "AGENT_SSH_DTSSH_COMPANION_CONFIG": str(config),
        "AGENT_SSH_DTSSH_COMPANION_ADOPT_POLL_SECONDS": "0.2",
    }

    process = subprocess.Popen(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not actions.exists():
            time.sleep(0.1)
        time.sleep(3)
        assert process.poll() is None, "companion must stay up while the adopted host lives"
        assert state.exists(), "a healthy host must not be stopped"
        assert actions.read_text(encoding="utf-8-sig").split() == ["status"]

        watchdog.kill()
        watchdog.wait(timeout=10)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        assert "adopted healthy dtssh host" in stdout
        assert actions.read_text(encoding="utf-8-sig").split() == ["status"]
    finally:
        if watchdog.poll() is None:
            watchdog.kill()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(not PWSH, reason="PowerShell is unavailable")
def test_companion_handles_missing_backup_root_in_config(tmp_path: Path) -> None:
    install_root = tmp_path / "local" / "agent-ssh-dtssh"
    config, state = _write_host_install(install_root, include_backup_root=False)
    env = {
        **os.environ,
        "LOCALAPPDATA": str(tmp_path / "local"),
        "AGENT_SSH_DTSSH_COMPANION_CONFIG": str(config),
    }

    process = subprocess.Popen(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not state.exists():
            time.sleep(0.1)
        assert state.exists(), "companion should start even without a backup root"
    finally:
        subprocess.run(
            [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COMPANION), "stop"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if process.poll() is None:
            process.wait(timeout=10)
