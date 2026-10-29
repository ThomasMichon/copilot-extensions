"""Contained entry controls: no native registration, runtime build or engine runs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _function(name: str) -> str:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    tail = source.split(f"function {name} {{", 1)[1]
    return f"function {name} {{" + re.split(r"\nfunction \w", tail, maxsplit=1)[0]


def _literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_ps(
    script: str, *, script_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if PWSH is None:
        pytest.skip("PowerShell is unavailable")
    arguments = ["-Command", script]
    if script_dir is not None:
        harness = script_dir / "harness.ps1"
        harness.write_text(script, encoding="utf-8")
        arguments = ["-File", str(harness)]
    return subprocess.run(  # noqa: S603
        [PWSH, "-NoProfile", "-NonInteractive", *arguments],
        capture_output=True, text=True, timeout=30, check=False,
        **no_window_kwargs(),
    )


@pytest.mark.parametrize("versioned", [False, True])
def test_shipped_engine_launcher_uses_engine_cli_only_for_foreground_run(tmp_path, versioned):
    home = tmp_path / "engine"
    home.mkdir()
    env_file = home / "engine.env"
    env_file.write_text("AGENT_INDEX_ENGINE_PORT=19421\n", encoding="utf-8")
    if versioned:
        (home / "current-version").write_text("retained-engine", encoding="utf-8")
    launcher = home / "engine.ps1"
    result = _run_ps(
        "$ErrorActionPreference = 'Stop'\n"
        f"$EngineHome = {_literal(home)}\n"
        f"$EngineEnvFile = {_literal(env_file)}\n"
        f"$EngineLauncher = {_literal(launcher)}\n"
        "$EngineVenvPython = 'engine-python'\n"
        "$utf8NoBom = New-Object System.Text.UTF8Encoding $false\n"
        "function Write-Ok { }\nfunction Write-Skip { }\n"
        "function Get-ActiveSlotPython { throw 'Unexpected primary lookup' }\n"
        "function engine-python {\n"
        " if ($args -contains 'resolve-python') {\n"
        "  Write-Host ('RESOLVE:' + ($args -join '|'))\n"
        "  $global:LASTEXITCODE = 0\n"
        "  return 'selected-engine-python'\n"
        " }\n"
        " Write-Host ('ENGINE:' + ($args -join '|'))\n"
        " Write-Host ('CONTEXT:' + $env:AGENT_INDEX_ENGINE_HOME + '|' + "
        "$env:AGENT_INDEX_ENGINE_PORT)\n"
        " $global:LASTEXITCODE = 23\n"
        "}\n"
        "function selected-engine-python { engine-python @args }\n"
        + _function("Write-EngineFiles")
        + "\nWrite-EngineFiles\n& $EngineLauncher\nexit $LASTEXITCODE\n"
    )
    assert result.returncode == 23, result.stderr
    assert "ENGINE:-I|-X|utf8|-m|agent_index|engine|run" in result.stdout
    assert f"CONTEXT:{home}|19421" in result.stdout
    assert ("RESOLVE:" in result.stdout) is versioned
    if versioned:
        assert f"|--root|{home}|resolve-python" in result.stdout
    assert env_file.read_text(encoding="utf-8") == "AGENT_INDEX_ENGINE_PORT=19421\n"
    assert not (home / ".runtime-bootstrap").exists()


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_embedded_cli_run_never_requests_a_detached_broker(monkeypatch, tmp_path, platform):
    from agent_index import __main__ as cli
    from agent_index.engine import daemon

    engine_python = tmp_path / "engine-python"
    engine_python.touch()
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("foreground embedded CLI attempted a detached launch")

    def foreground(argv):
        calls.append(list(argv))
        return SimpleNamespace(wait=lambda: 23)

    class ReplacedProcess(Exception):
        pass

    def replace(executable, argv):
        assert executable == str(engine_python)
        calls.append(list(argv))
        raise ReplacedProcess

    monkeypatch.setenv("AGENT_INDEX_ENGINE_HOME", str(tmp_path / "engine"))
    monkeypatch.setenv("AGENT_INDEX_ENGINE_HOST", "127.0.0.7")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_PORT", "19421")
    monkeypatch.setattr(daemon, "engine_venv_python", lambda _home: engine_python)
    monkeypatch.setattr(daemon, "windowless_python", lambda python: python)
    monkeypatch.setattr(daemon, "spawn_daemon", forbidden)
    monkeypatch.setattr(cli, "spawn_daemon", forbidden)
    monkeypatch.setattr(daemon, "os", SimpleNamespace(
        name=platform, environ=os.environ, execv=replace,
    ))
    monkeypatch.setattr(daemon.subprocess, "Popen", foreground)
    if platform == "nt":
        assert cli.main(["engine", "run"]) == 23
    else:
        with pytest.raises(ReplacedProcess):
            cli.main(["engine", "run"])
    assert calls == [[
        str(engine_python), "-m", "agent_index_engine.app",
        "--host", "127.0.0.7", "--port", "19421",
    ]]


@pytest.mark.parametrize("mode", ["cold", "warm", "missing-primary", "no-service"])
def test_no_task_ensure_uses_primary_manager_and_restores_engine_context(tmp_path, mode):
    env_file = tmp_path / "engine.env"
    env_file.write_text("AGENT_INDEX_ENGINE_PORT=19421\n", encoding="utf-8")
    result = _run_ps(
        f"$Mode = '{mode}'\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$EngineVenvPython = 'engine-python'; $EngineHome = 'engine-home'\n"
        f"$EngineEnvFile = {_literal(env_file)}\n"
        "$NoService = $Mode -eq 'no-service'\n"
        "$env:AGENT_INDEX_ENGINE_HOME = 'caller-home'\n"
        "$env:AGENT_INDEX_ENGINE_PORT = 'caller-port'\n"
        "$env:AGENT_INDEX_INSTALL_DIR = 'primary-root'\n"
        "function Get-ActiveSlotPython { return 'primary-python' }\n"
        "function Test-Path {\n"
        " param($Path, $LiteralPath)\n"
        " if ($LiteralPath) { return $true }\n"
        " return $Path -eq 'engine-python' -or\n"
        "  ($Path -eq 'primary-python' -and $Mode -ne 'missing-primary')\n"
        "}\n"
        "function Test-EnginePort { Write-Host 'PROBE'; return $Mode -eq 'warm' }\n"
        "function Write-EngineFiles { Write-Host 'FILES' }\n"
        "function Write-Ok { param($Msg) Write-Host \"OK:$Msg\" }\n"
        "function Write-Skip { param($Msg) Write-Host \"SKIP:$Msg\" }\n"
        "function Write-Warn { param($Msg) Write-Host \"WARN:$Msg\" }\n"
        "function engine-python { throw 'Engine-local manager invoked' }\n"
        "function primary-python {\n"
        " Write-Host ('PRIMARY:' + ($args -join '|'))\n"
        " Write-Host ('CONTEXT:' + $env:AGENT_INDEX_ENGINE_HOME + '|' +\n"
        "  $env:AGENT_INDEX_ENGINE_PORT + '|' + $env:AGENT_INDEX_INSTALL_DIR)\n"
        " $global:LASTEXITCODE = 0\n"
        "}\n"
        + _function("Invoke-WithEnvFile") + "\n" + _function("Ensure-EngineRunning")
        + "\nEnsure-EngineRunning\n"
        "Write-Host ('RESTORED:' + $env:AGENT_INDEX_ENGINE_HOME + '|' +\n"
        " $env:AGENT_INDEX_ENGINE_PORT + '|' + $env:AGENT_INDEX_INSTALL_DIR)\n"
    )
    assert result.returncode == 0, result.stderr
    assert "RESTORED:caller-home|caller-port|primary-root" in result.stdout
    if mode == "cold":
        assert "PRIMARY:-I|-X|utf8|-m|agent_index|engine|start|--strict" in result.stdout
        assert "CONTEXT:engine-home|19421|primary-root" in result.stdout
    else:
        assert "PRIMARY:" not in result.stdout
    if mode in ("missing-primary", "no-service"):
        assert "FILES" not in result.stdout
        assert "PROBE" not in result.stdout
    if mode == "missing-primary":
        assert "WARN:Index runtime not installed" in result.stdout
    if mode == "warm":
        assert "leaving the warm engine untouched" in result.stdout


@pytest.mark.parametrize("role", ["host", "client", "unconfigured"])
def test_default_role_policy_does_not_turn_engine_presence_into_host_authority(role):
    result = _run_ps(
        f"function Get-ActivationRole {{ return '{role}' }}\n"
        "function Ensure-EngineRunning { Write-Host 'ENGINE' }\n"
        "function Ensure-ServiceRunning { Write-Host 'SERVICE' }\n"
        + _function("Ensure-Running") + "\nEnsure-Running\n"
    )
    assert result.returncode == 0, result.stderr
    assert ("ENGINE" in result.stdout) is (role == "host")
    assert "SERVICE" in result.stdout


def test_completed_engine_reuse_never_constructs_or_adopts_helpers(tmp_path):
    home = tmp_path / "engine"
    slot = home / "versions" / "retained-engine"
    python = slot / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"immutable engine fixture")
    marker = home / "current-version"
    marker.write_text("retained-engine", encoding="utf-8")
    before = {str(path.relative_to(home)): path.read_bytes()
              for path in home.rglob("*") if path.is_file()}
    result = _run_ps(
        "$ErrorActionPreference = 'Stop'\n"
        f"$EngineHome = {_literal(home)}\n"
        "$env:AGENT_INDEX_NO_ENGINE_DEPS = '0'\n"
        "function Get-BootstrapPython { return 'bootstrap-python' }\n"
        "function bootstrap-python {\n"
        f" Write-Output {_literal(python)}\n"
        " $global:LASTEXITCODE = 0\n"
        "}\n"
        "function Get-IndexUv { throw 'Construction must not run' }\n"
        "function Invoke-RuntimeBrokerAdoption { throw 'Adoption must not run' }\n"
        "function Write-Skip { param($Msg) Write-Host \"SKIP:$Msg\" }\n"
        + _function("Install-Engine")
        + "\nif (-not (Install-Engine)) { throw 'Reuse refused' }\n"
        "Write-Host ('SELECTED:' + $EngineVenvPython)\n",
        script_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Completed engine runtime preserved" in result.stdout
    assert f"SELECTED:{python}" in result.stdout
    assert before == {str(path.relative_to(home)): path.read_bytes()
                      for path in home.rglob("*") if path.is_file()}
