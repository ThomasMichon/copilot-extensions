"""Qualify the real task-launcher producer without touching ScheduledTasks."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SUPPORT = ("bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe")
pytestmark = pytest.mark.portfolio_tier("T3")
if sys.platform == "win32":
    NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    NO_WINDOW = 0


def _function(name: str) -> str:
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8-sig")
    start = source.index(f"function {name} {{")
    return source[start:source.index("\nfunction ", start + 1)]


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class ServiceFixture:
    root: Path
    scripts: Path
    publish: Path
    environment: dict[str, str]
    managed_python: Path
    uv: Path


@pytest.fixture(scope="module")
def windows_service(tmp_path_factory: pytest.TempPathFactory) -> ServiceFixture:
    if os.name != "nt":
        pytest.skip("native Windows service ingress")
    uv = shutil.which("uv")
    assert uv, "explicitly prepare UV before the native service controls"
    folder = tmp_path_factory.mktemp("vault-service")
    root = folder / "vault space % dollar$ 'quoted'-\u03bb"
    scripts = folder / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in SUPPORT:
        shutil.copyfile(SCRIPTS / name, scripts / name)
    tools = folder / "prepared-tools"
    tools.mkdir()
    bound_uv = tools / "chosen-uv.exe"
    shutil.copyfile(uv, bound_uv)
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX",
        }
    }
    environment.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never")
    prepared = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=environment, capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
    )
    assert prepared.returncode == 0, prepared.stderr
    for version in ("1.0.0", "2.0.0"):
        slot = root / "versions" / version
        subprocess.run(
            [uv, "venv", "--no-project", "--python", prepared.stdout.strip(),
             "--no-python-downloads", str(slot)],
            env=environment, check=True, capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW,
        )
        packages = slot / "Lib/site-packages"
        vault = packages / "agent_vault"
        vault.mkdir()
        (vault / "__init__.py").write_text("", encoding="utf-8")
        (vault / "service_arguments.py").write_text("", encoding="utf-8")
        (vault / "configuration.py").write_text(
            "def service_configuration_snapshot(project):\n"
            " return {'root':project, 'config':project+'/selected.json', 'native_default_owner':'1'}\n",
            encoding="utf-8",
        )
        (vault / "invocation.py").write_text(
            "def invocation_contract(): return {'capabilities':['native-default-owner-v1']}\n",
            encoding="utf-8",
        )
        (vault / "service.py").write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            "print(json.dumps({'python':sys.executable,'base':sys._base_executable,\n"
            " 'prefix':sys.prefix,'pid':os.getpid(),'parent':os.getppid(),\n"
            " 'argv':sys.argv[1:],'cwd':os.getcwd(),'stdin':sys.stdin.read(),\n"
            " 'cache':sys.pycache_prefix,'home':os.environ.get('AGENT_VAULT_HOME'),\n"
            " 'config':os.environ.get('AGENT_VAULT_CONFIG'),\n"
            " 'installation':os.environ.get('AGENT_VAULT_INSTALLATION_ID'),\n"
            " 'port':os.environ.get('AGENT_VAULT_PORT'),\n"
            " 'pythonhome':os.environ.get('PYTHONHOME'),'pythonpath':os.environ.get('PYTHONPATH')}))\n"
            "print('fixture worker stderr',file=sys.stderr)\n"
            "sys.exit(23)\n",
            encoding="utf-8",
        )
        cache = packages / "agent_procutil"
        cache.mkdir()
        (cache / "__init__.py").write_text("", encoding="utf-8")
        (cache / "python_cache.py").write_text(
            "from pathlib import Path\nPOLICY_VERSION=2\n"
            "def policy_arguments(root, *, runtime):\n"
            " Path(root,'python-probed').write_text(runtime)\n"
            " return ['-X','pycache_prefix='+str(Path(root,'cache space'))]\n",
            encoding="utf-8",
        )
    (root / "bin").mkdir()
    (root / "bin/resolve-runtime.ps1").write_text(
        "$version=[IO.File]::ReadAllText((Join-Path $env:AGENT_RT_ROOT 'current-version')).Trim()\n"
        "$AgentRtPy=Join-Path $env:AGENT_RT_ROOT ('versions\\'+$version+'\\Scripts\\python.exe')\n",
        encoding="utf-8-sig",
    )
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    publish = scripts / "publish.ps1"
    publish.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir={_quote(root)}; $RunDir=Join-Path $InstallDir 'run'\n"
        "$TaskLauncher=Join-Path $InstallDir 'service.ps1'; $TaskName='FixtureVault'\n"
        "$SocketPath='fixture'; $PipePath='fixture'; $PidFile='fixture'; $LogFile='fixture'\n"
        "$installationId='fixture-installation'; $Action='update'; $NoService=$false\n"
        f"$LinkPython={_quote(root / 'versions/1.0.0/Scripts/python.exe')}\n"
        f"$WheelProfile='fixture'; $WheelUv={_quote(bound_uv)}\n"
        "function Write-Ok { param($Message) }\n"
        "function Write-Skip { param($Message) }\n"
        "function Write-Warn { param($Message) throw $Message }\n"
        "function Write-Fail { param($Message) throw $Message }\n"
        "function Get-WindowsScheduledTaskPolicy { 'preserve' }\n"
        "function Get-SchedulingPolicyTask { param($Policy) $null }\n"
        "function Test-LegacyVaultTask { throw 'no incumbent task' }\n"
        "function Register-ScheduledTask { throw 'task mutation forbidden' }\n"
        "function Set-ScheduledTask { throw 'task mutation forbidden' }\n"
        "function Start-ScheduledTask { throw 'task start forbidden' }\n"
        "function Stop-ScheduledTask { throw 'task stop forbidden' }\n"
        "function powershell.exe { $global:LASTEXITCODE=0 }\n"
        + _function("Get-VaultServiceConfiguration")
        + _function("Publish-RuntimeHelperFile")
        + _function("Register-AgentVaultTask")
        + "\nRegister-AgentVaultTask\n",
        encoding="utf-8-sig",
    )
    environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(folder / "configuration space")
    manager = folder / "no-python-no-uv"
    manager.mkdir()
    environment["PROBE_MANAGER_PATH"] = str(manager)
    environment["PROBE_AMBIENT_UV_MARKER"] = str(root / "ambient-uv-called")
    for name in ("uv", "python"):
        (manager / f"{name}.cmd").write_text(
            '@echo off\r\n> "%PROBE_AMBIENT_UV_MARKER%" echo called\r\nexit /b 94\r\n',
            encoding="ascii",
        )
    return ServiceFixture(root, scripts, publish, environment, Path(prepared.stdout.strip()), bound_uv)


@pytest.fixture(params=("powershell", "pwsh"))
def service_shell(request: pytest.FixtureRequest) -> str:
    host = shutil.which(str(request.param))
    assert host, f"native {request.param} is required"
    return host


def _run(host: str, script: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=NO_WINDOW,
    )


def _publish(fixture: ServiceFixture, host: str) -> Path:
    result = _run(host, fixture.publish, fixture.environment)
    assert result.returncode == 0, result.stderr
    for name in SUPPORT:
        assert (fixture.root / "bin" / name).read_bytes() == (fixture.scripts / name).read_bytes()
    return fixture.root / "service.ps1"


@pytest.mark.parametrize("selection", ("resolver", "fallback"))
def test_windows_vault_service_uses_prepared_uv_before_python(
    windows_service: ServiceFixture, service_shell: str, tmp_path: Path, selection: str,
) -> None:
    fixture = windows_service
    launcher = _publish(fixture, service_shell)
    resolver = fixture.root / "bin/resolve-runtime.ps1"
    original = resolver.read_bytes()
    environment = dict(fixture.environment, PATH=fixture.environment["PROBE_MANAGER_PATH"],
                       PYTHONHOME="unusable-python-home", PYTHONPATH="unusable-python-path",
                       UV_PYTHON="unusable-python-selection", VIRTUAL_ENV="unusable-venv",
                       UV_CONFIG_FILE="unusable-uv-configuration")
    runner = tmp_path / "run.ps1"
    runner.write_text(
        f"[Console]::Error.WriteLine('manager-pid='+$PID)\n& {_quote(launcher)} "
        "'space value' 'dollar$; & literal'\nexit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    if selection == "fallback":
        resolver.unlink()
    try:
        for version in ("1.0.0", "2.0.0"):
            (fixture.root / "current-version").write_text(version, encoding="utf-8")
            result = subprocess.run(
                [service_shell, "-NoProfile", "-NonInteractive", "-File", str(runner)],
                env=environment, input="worker stdin \u03bb\n", capture_output=True,
                text=True, encoding="utf-8", timeout=20, creationflags=NO_WINDOW,
            )
            assert result.returncode == 23, result.stderr
            observed = json.loads(result.stdout)
            slot = fixture.root / "versions" / (version if selection == "resolver" else "1.0.0")
            assert Path(observed["python"]) == slot / "Scripts/python.exe"
            assert Path(observed["base"]) == fixture.managed_python
            assert Path(observed["prefix"]) == slot
            parent = int(result.stderr.split("manager-pid=")[1].splitlines()[0])
            assert observed["parent"] == parent and observed["pid"] != parent
            assert Path(observed["cwd"]) == Path.cwd()
            assert observed["stdin"] == "worker stdin \u03bb\n"
            assert observed["argv"] == [
                "--foreground", "--persistent", "--configuration-root",
                environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"], "--native-default-owner",
                "space value", "dollar$; & literal",
            ]
            assert observed["home"] == str(fixture.root)
            assert observed["config"] == environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] + "/selected.json"
            assert observed["installation"] == "fixture-installation" and observed["port"] == "0"
            assert observed["pythonhome"] is None and observed["pythonpath"] is None
            assert Path(observed["cache"]) == fixture.root / "cache space"
            assert (fixture.root / "python-probed").read_text() == observed["python"]
            assert "fixture worker stderr" in result.stderr
            assert not (fixture.root / "ambient-uv-called").exists()
    finally:
        resolver.write_bytes(original)


@pytest.mark.parametrize("missing", ("uv", "runtime", "bootstrap-uv.ps1", "bootstrap-capture.exe"))
def test_windows_vault_missing_preparation_refuses_before_python(
    windows_service: ServiceFixture, service_shell: str, missing: str,
) -> None:
    fixture = windows_service
    launcher = _publish(fixture, service_shell)
    (fixture.root / "current-version").write_text("1.0.0", encoding="utf-8")
    paths = {
        "uv": fixture.uv,
        "runtime": fixture.root / "versions/1.0.0/Scripts/python.exe",
    }
    path = paths.get(missing, fixture.root / "bin" / missing)
    held = path.with_name(path.name + ".held")
    probe = fixture.root / "python-probed"
    probe.unlink(missing_ok=True)
    path.rename(held)
    try:
        result = _run(
            service_shell, launcher,
            dict(fixture.environment, PATH=fixture.environment["PROBE_MANAGER_PATH"]),
        )
        assert result.returncode != 0
        assert not probe.exists() and not result.stdout.strip()
        assert not (fixture.root / "ambient-uv-called").exists()
    finally:
        held.rename(path)


def test_windows_vault_publication_is_atomic_and_idempotent(
    windows_service: ServiceFixture, service_shell: str,
) -> None:
    fixture = windows_service
    launcher = _publish(fixture, service_shell)
    paths = [launcher, *(fixture.root / "bin" / name for name in SUPPORT)]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    _publish(fixture, service_shell)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    source = fixture.scripts / "bootstrap-capture.exe"
    held = source.with_suffix(".held")
    source.rename(held)
    try:
        result = _run(service_shell, fixture.publish, fixture.environment)
        assert result.returncode != 0
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
        assert set(fixture.root.glob(".service.ps1.*")) == set()
    finally:
        held.rename(source)
