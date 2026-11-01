"""Contained entry controls: no native registration, runtime build or engine runs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs, python_cache_args

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


@pytest.mark.skipif(os.name != "nt", reason="Windows lexical cache paths")
@pytest.mark.parametrize("root", [
    r"C:\owned index\state\..\root",
    r"\\server\share\owned index",
    r"\\?\C:\owned index",
    r"\\?\UNC\server\share\owned index",
])
def test_native_cache_arguments_match_owned_lexical_python_contract(root: str) -> None:
    result = _run_ps(
        _function("Get-IndexPythonCacheArgs")
        + f"\n@(Get-IndexPythonCacheArgs -Root {_literal(Path(root))})"
        " | ConvertTo-Json -Compress\n"
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[0] == "-X" and len(args) == 2
    cache = Path(args[1].removeprefix("pycache_prefix="))
    assert cache.parent == Path(python_cache_args(root)[1].partition("=")[2]).parent
    assert re.fullmatch(r"unsealed-[0-9a-f]{32}", cache.name)


@pytest.mark.parametrize("root", ["relative-root", r"C:relative-root"])
def test_native_cache_arguments_refuse_relative_roots(root: str) -> None:
    result = _run_ps(
        _function("Get-IndexPythonCacheArgs")
        + f"\ntry {{ Get-IndexPythonCacheArgs -Root '{root}' }}"
        " catch { Write-Output $_; exit 23 }\n"
    )
    assert result.returncode == 23, result.stderr
    assert "absolute owned runtime root" in result.stdout


@pytest.mark.parametrize("prospective_exists", [False, True])
@pytest.mark.parametrize("route", ["explicit", "restart-stop-failed"])
def test_actual_stop_keeps_selected_slot_immutable_with_cold_warm_cache(
    tmp_path: Path, prospective_exists: bool, route: str,
) -> None:
    root = tmp_path / "owned index"
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    if os.name == "nt":
        python = slot / "Scripts" / "python.exe"
        site = slot / "Lib" / "site-packages"
    else:
        python = slot / "bin" / "python"
        site = slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    package = site / "agent_index"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "probe.py").write_text("VALUE = 'inert selected runtime'\n", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\nfrom . import probe\n"
        "print(json.dumps({'args': sys.argv[1:], 'prefix': sys.pycache_prefix,"
        " 'disabled': sys.dont_write_bytecode, 'runtime': sys.prefix,"
        " 'cwd': os.getcwd(), 'rt_root': os.environ.get('AGENT_RT_ROOT'),"
        " 'engine_home': os.environ.get('AGENT_INDEX_ENGINE_HOME'),"
        " 'value': probe.VALUE}))\n"
        "sys.exit(23 if sys.argv[1:] == ['engine', 'stop'] else 17)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "fixture", "pid": 0,
    }), encoding="utf-8")
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    resolver = root / "bin" / "resolve-runtime.ps1"
    resolver.parent.mkdir()
    resolver.write_bytes((PLUGIN / "scripts" / "resolve-runtime.ps1").read_bytes())
    prospective = root / "versions" / "9.0.0" / python.relative_to(slot)
    if prospective_exists:
        prospective.parent.mkdir(parents=True)
        prospective.write_bytes(b"prospective interpreter must never run")
    engine = tmp_path / "independent engine"
    engine.mkdir()
    (engine / "engine.pid").write_text("inert engine identity", encoding="utf-8")
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$InstallDir = {_literal(root)}\n$LinkPython = {_literal(prospective)}\n"
        "$TaskName = 'inert-service'; $EngineTaskName = 'inert-engine'\n"
        "$env:AGENT_RT_ROOT = 'caller-root'\n"
        f"$env:AGENT_INDEX_ENGINE_HOME = {_literal(engine)}\n"
        f"Set-Location -LiteralPath {_literal(tmp_path)}\n"
        "function Get-ScheduledTask { return $null }\n"
        "function Stop-ScheduledTask { throw 'Forbidden task stop' }\n"
        + _function("Get-IndexPythonCacheArgs") + "\n"
    )
    if route == "explicit":
        script += _function("Invoke-Stop") + "\nInvoke-Stop\nexit $LASTEXITCODE\n"
    else:
        script += (
            f"$EngineVenvPython = {_literal(python)}\n$NoService = $false\n"
            "function Start-Sleep { throw 'Failed stop must not restart' }\n"
            + _function("Get-ActiveSlotPython") + "\n" + _function("Restart-EngineDaemon")
            + "\ntry { Restart-EngineDaemon }"
            " catch { Write-Host ('REFUSED:' + $_); exit $LASTEXITCODE }\n"
        )
    previous = None
    cache_root = Path(python_cache_args(root)[1].partition("=")[2]).parent
    for _cycle in range(2):
        result = _run_ps(script)
        assert result.returncode == 23, result.stderr
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        assert [record["args"] for record in records] == (
            [["stop"], ["engine", "stop"]] if route == "explicit" else [["engine", "stop"]]
        )
        if route != "explicit":
            assert "REFUSED:Primary index runtime failed to stop the engine" in result.stdout
        for record in records:
            cache = Path(record["prefix"])
            assert cache.parent == cache_root
            assert re.fullmatch(r"unsealed-[0-9a-f]{32}", cache.name)
            assert not record["disabled"]
            assert Path(record["runtime"]) == slot
            assert Path(record["cwd"]) == tmp_path
            assert record["rt_root"] == "caller-root"
            assert Path(record["engine_home"]) == engine
        assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
        assert not list(slot.rglob("*.pyc"))
        assert not list(engine.rglob("*.pyc"))
        caches = list(cache_root.rglob("*.pyc"))
        assert caches and any(path.name.startswith("probe.") for path in caches)
        current = {str(path): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
                   for path in caches}
        if previous is not None:
            assert all(current[path] == content for path, content in previous.items())
            assert set(current) > set(previous)
        previous = current
    probe = package / "probe.py"
    probe_stat = probe.stat()
    probe.write_text("VALUE = 'fresh selected runtime'\n", encoding="utf-8")
    os.utime(probe, ns=(probe_stat.st_atime_ns, probe_stat.st_mtime_ns))
    result = _run_ps(script)
    assert result.returncode == 23, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert records and all(record["value"] == "fresh selected runtime" for record in records)
    assert all(
        (Path(path).read_bytes(), Path(path).stat().st_mtime_ns, Path(path).stat().st_ino) == content
        for path, content in previous.items()
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

    def foreground(argv, *, instance):
        assert instance.runtime_root == tmp_path / "engine"
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
        name=platform, environ=os.environ, path=os.path, execv=replace,
    ))
    monkeypatch.setattr(daemon, "spawn_instance", foreground)
    if platform == "nt":
        assert cli.main(["engine", "run"]) == 23
    else:
        with pytest.raises(ReplacedProcess):
            cli.main(["engine", "run"])
    from agent_procutil import python_cache_args
    assert len(calls) == 1
    command = calls[0]
    assert command[0] == str(engine_python) and command[1] == "-X"
    cache = Path(command[2].removeprefix("pycache_prefix="))
    assert cache.parent == Path(python_cache_args(tmp_path / "engine")[1].partition("=")[2]).parent
    assert command[3:] == [
        "-m", "agent_index_engine.app", "--host", "127.0.0.7", "--port", "19421",
    ]


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
