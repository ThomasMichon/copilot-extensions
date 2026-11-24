"""Exercise the generated Windows service script without registering a task."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import select
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins/agent-bridge/scripts"
if sys.platform == "win32":
    NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    NO_WINDOW = 0
pytestmark = pytest.mark.portfolio_tier("T3")


def _function(name: str) -> str:
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    start = text.index(f"function {name} {{")
    end = text.index("\nfunction ", start + 1)
    return text[start:end]


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(host: str, script: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=NO_WINDOW,
    )


@dataclass(frozen=True)
class ServiceFixture:
    root: Path
    scripts: Path
    publish: Path
    environment: dict[str, str]
    managed_python: Path
    uv: Path


def _publication_source(root: Path) -> str:
    return (
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={_quote(root)}; $PidFile=Join-Path $InstallDir 'agent-bridge.pid'\n"
        "function Write-Ok { param($Message) }\n"
        "function Write-Skip { param($Message) }\n"
        "function Get-WindowsScheduledTaskPolicy { 'preserve' }\n"
        "function Get-SchedulingPolicyTask { param($Policy)\n"
        " [pscustomobject]@{State='Running'; Principal=[pscustomobject]@{LogonType='S4U'}} }\n"
        "function Register-ScheduledTask_ { throw 'task registration forbidden' }\n"
        "function Set-ScheduledTask { throw 'task mutation forbidden' }\n"
        "function Start-ScheduledTask { throw 'task start forbidden' }\n"
        "function Stop-ScheduledTask { throw 'task stop forbidden' }\n"
        + _function("Publish-RuntimeHelperFile")
        + _function("Write-BridgeServiceLauncher")
        + _function("Ensure-ScheduledTask")
        + "\nEnsure-ScheduledTask -RefreshLauncher\n"
    )


@pytest.fixture(scope="module")
def windows_service(tmp_path_factory: pytest.TempPathFactory) -> ServiceFixture:
    if os.name != "nt":
        pytest.skip("native Windows service entrypoint")
    uv = shutil.which("uv")
    assert uv, "Explicitly prepare UV before the native service controls"
    folder = tmp_path_factory.mktemp("bridge-service")
    root = folder / "bridge space % dollar$ 'quoted'"
    root.mkdir()
    scripts = folder / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in ("bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    prepared_tools = folder / "prepared-tools"
    prepared_tools.mkdir()
    bound_uv = prepared_tools / "chosen-uv.exe"
    shutil.copyfile(uv, bound_uv)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    environment.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never")
    prepared = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=environment, capture_output=True, text=True, timeout=15,
        creationflags=NO_WINDOW,
    )
    if prepared.returncode:
        pytest.skip("Explicitly prepare managed CPython 3.11.18 before offline execution")
    for version in ("1.0.0", "2.0.0"):
        slot = root / "versions" / version
        subprocess.run(
            [uv, "venv", "--no-project", "--python", prepared.stdout.strip(),
             "--no-python-downloads", str(slot)],
            env=environment, check=True, capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW,
        )
        packages = slot / "Lib/site-packages"
        (packages / "agent_bridge.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "print('fixture worker stdout', flush=True)\n"
            "print('fixture worker stderr', file=sys.stderr, flush=True)\n"
            "output=Path(os.environ['WORKER_CALLS'])\n"
            "pending=output.with_suffix('.pending')\n"
            "pending.write_text(json.dumps({\n"
            " 'python':sys.executable, 'base':sys._base_executable, 'prefix':sys.prefix,\n"
            " 'pid':os.getpid(), 'parent':os.getppid(), 'argv':sys.argv[1:], 'cwd':os.getcwd(),\n"
            " 'root':os.environ.get('AGENT_BRIDGE_INSTALL_DIR'),\n"
            " 'config':os.environ.get('AGENT_BRIDGE_CONFIG_DIR'),\n"
            " 'connect':os.environ.get('AGENT_BRIDGE_CONNECT_LOG'),\n"
            " 'path':os.environ.get('PATH'),\n"
            " 'pythonhome':os.environ.get('PYTHONHOME'),\n"
            " 'pythonpath':os.environ.get('PYTHONPATH'),\n"
            " 'cache':sys.pycache_prefix,\n"
            "}))\n"
            "pending.replace(output)\n",
            encoding="utf-8",
        )
        cache = packages / "agent_procutil"
        cache.mkdir()
        (cache / "__init__.py").write_text("", encoding="utf-8")
        (cache / "python_cache.py").write_text(
            "from pathlib import Path\n"
            "POLICY_VERSION=2\n"
            "def policy_arguments(root, *, runtime):\n"
            " Path(root, 'cache-runtime').write_text(runtime)\n"
            " return ['-X', 'pycache_prefix=' + str(Path(root, 'cache space'))]\n",
            encoding="utf-8",
        )
    publish = scripts / "publish.ps1"
    publish.write_text(
        "$WheelProfile='fixture'; $WheelUv=" + _quote(bound_uv) + "\n"
        + _publication_source(root),
        encoding="utf-8-sig",
    )
    environment.update(
        PATH=str(prepared_tools) + os.pathsep + environment["PATH"],
        WORKER_CALLS=str(root / "worker.json"),
        PROBE_MANAGER_PATH=str(folder / "manager-no-python-no-uv"),
        PROBE_AMBIENT_UV_MARKER=str(root / "ambient-uv-called"),
    )
    manager = Path(environment["PROBE_MANAGER_PATH"])
    manager.mkdir()
    (manager / "python.cmd").write_text(
        '@echo off\r\nexit /b 93\r\n', encoding="ascii",
    )
    (manager / "uv.cmd").write_text(
        '@echo off\r\n> "%PROBE_AMBIENT_UV_MARKER%" echo called\r\nexit /b 94\r\n',
        encoding="ascii",
    )
    return ServiceFixture(
        root, scripts, publish, environment, Path(prepared.stdout.strip()), bound_uv,
    )


@pytest.fixture(params=("powershell", "pwsh"))
def service_shell(request: pytest.FixtureRequest) -> str:
    host = shutil.which(str(request.param))
    if not host:
        pytest.skip(f"{request.param} is unavailable")
    return host


def _publish(fixture: ServiceFixture, host: str) -> Path:
    result = _run(host, fixture.publish, fixture.environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (fixture.root / "ambient-uv-called").exists()
    launcher = fixture.root / "start-agent-bridge.ps1"
    assert launcher.is_file()
    return launcher


def _launch(
    fixture: ServiceFixture, host: str, *, resolve_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    harness = fixture.scripts / "launch.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$env:PATH=$env:PROBE_MANAGER_PATH\n"
        f"& {_quote(fixture.root / 'start-agent-bridge.ps1')}"
        + (" -ResolveOnly" if resolve_only else "") + "\n",
        encoding="utf-8-sig",
    )
    environment = {
        **fixture.environment, "UV_PYTHON": "poison", "UV_PYTHON_PREFERENCE": "only-system",
        "UV_CONFIG_FILE": "missing-relative-config.toml", "UV_NO_CONFIG": "false",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison",
        "PYTHONHOME": "poison", "PYTHONPATH": "poison",
    }
    return _run(host, harness, environment)


def _clear_worker(fixture: ServiceFixture) -> None:
    for name in ("agent-bridge.pid", "worker.json", "cache-runtime"):
        (fixture.root / name).unlink(missing_ok=True)


def test_windows_service_updates_existing_task_launcher_idempotently(
    windows_service: ServiceFixture, service_shell: str,
) -> None:
    fixture = windows_service
    launcher = _publish(fixture, service_shell)
    launcher.write_text("# obsolete startup\n", encoding="utf-8")
    _publish(fixture, service_shell)
    before = launcher.read_bytes(), launcher.stat().st_mtime_ns
    helpers = {path: path.stat().st_mtime_ns for path in (fixture.root / "bin").iterdir()}
    _publish(fixture, service_shell)
    assert (launcher.read_bytes(), launcher.stat().st_mtime_ns) == before
    assert {path: path.stat().st_mtime_ns for path in helpers} == helpers
    assert b"# obsolete startup" not in before[0]
    assert not list(fixture.root.glob(".start-agent-bridge.ps1.*"))
    assert {path.name for path in (fixture.root / "bin").iterdir()} == {
        "bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe",
    }


def test_windows_service_real_uv_keeps_offline_selected_worker(
    windows_service: ServiceFixture, service_shell: str, request: pytest.FixtureRequest,
) -> None:
    fixture = windows_service
    _publish(fixture, service_shell)
    (fixture.root / "last-known-good").write_text("1.0.0\n", encoding="utf-8")
    samples = []
    for selection in ("current", "lkg", "newest"):
        (fixture.root / "current-version").write_text(
            "1.0.0\n" if selection == "current" else "missing\n", encoding="utf-8",
        )
        (fixture.root / "last-known-good").write_text(
            "missing\n" if selection == "newest" else "1.0.0\n", encoding="utf-8",
        )
        version = "2.0.0" if selection == "newest" else "1.0.0"
        expected = fixture.root / "versions" / version / "Scripts/python.exe"
        for _cycle in range(2):
            _clear_worker(fixture)
            started = time.perf_counter()
            result = _launch(fixture, service_shell)
            samples.append(time.perf_counter() - started)
            assert result.returncode == 0, result.stdout + result.stderr
            assert not (fixture.root / "ambient-uv-called").exists()
            deadline = time.monotonic() + 5
            worker_file = fixture.root / "worker.json"
            while not worker_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            worker = json.loads(worker_file.read_text())
            assert Path(worker.pop("base")).samefile(fixture.managed_python)
            assert Path(worker.pop("python")).samefile(expected)
            assert Path(worker.pop("prefix")).samefile(expected.parents[1])
            assert Path((fixture.root / "cache-runtime").read_text()).samefile(expected)
            published_pid = int((fixture.root / "agent-bridge.pid").read_text().strip())
            assert published_pid in (worker.pop("pid"), worker.pop("parent"))
            assert worker == {
                "argv": ["start"], "cwd": str(fixture.root),
                "root": str(fixture.root), "config": str(fixture.root),
                "connect": str(fixture.root / "logs/connect.log"),
                "path": fixture.environment["PROBE_MANAGER_PATH"],
                "pythonhome": None, "pythonpath": None,
                "cache": str(fixture.root / "cache space"),
            }
            assert (fixture.root / "agent-bridge.log").read_text().strip() == "fixture worker stdout"
            assert (fixture.root / "agent-bridge-err.log").read_text().strip() == "fixture worker stderr"
    request.node.user_properties.append(("bridge_windows_service_performance", json.dumps({
        "shell": Path(service_shell).stem, "samples_seconds": samples, "cycles": 2,
        "selections": ["current", "lkg", "newest"], "network": "no-downloads",
        "runtime": "managed-venv",
    })))


def test_windows_service_readonly_and_healthy_pid_need_no_uv(
    windows_service: ServiceFixture, service_shell: str,
) -> None:
    fixture = windows_service
    _publish(fixture, service_shell)
    expected = fixture.root / "versions/1.0.0/Scripts/python.exe"
    (fixture.root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    uv = fixture.uv
    parked = uv.with_suffix(".held")
    uv.rename(parked)
    try:
        _clear_worker(fixture)
        resolved = _launch(fixture, service_shell, resolve_only=True)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert Path(resolved.stdout.strip()).samefile(expected)
        (fixture.root / "agent-bridge.pid").write_text(str(os.getpid()), encoding="utf-8")
        healthy = _launch(fixture, service_shell)
        assert healthy.returncode == 0, healthy.stdout + healthy.stderr
        assert not (fixture.root / "worker.json").exists()
        assert not (fixture.root / "cache-runtime").exists()
    finally:
        parked.rename(uv)
        _clear_worker(fixture)


def test_windows_service_missing_prerequisites_refuse_before_python(
    windows_service: ServiceFixture, service_shell: str,
) -> None:
    fixture = windows_service
    _publish(fixture, service_shell)
    (fixture.root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    uv = fixture.uv
    for path in (
        uv, fixture.root / "bin/bootstrap-uv.ps1",
        fixture.root / "bin/bootstrap-capture.ps1", fixture.root / "bin/bootstrap-capture.exe",
    ):
        _clear_worker(fixture)
        parked = path.with_suffix(path.suffix + ".held")
        path.rename(parked)
        try:
            result = _launch(fixture, service_shell)
            assert result.returncode != 0, result.stdout + result.stderr
            assert not (fixture.root / "worker.json").exists()
            assert not (fixture.root / "cache-runtime").exists()
            assert not (fixture.root / "agent-bridge.pid").exists()
        finally:
            parked.rename(path)


def test_service_publisher_refreshes_script_without_task_mutation(tmp_path: Path) -> None:
    host = shutil.which("pwsh")
    uv = shutil.which("uv")
    if not host or not uv:
        pytest.skip("PowerShell Core and prepared UV are required for publication")
    root = tmp_path / "bridge space % dollar$ 'quoted'"
    root.mkdir()
    scripts = tmp_path / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in ("bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    launcher = root / "start-agent-bridge.ps1"
    launcher.write_text("# obsolete startup\n", encoding="utf-8")
    publish = scripts / "publish.ps1"
    publish.write_text(
        _publication_source(root)
        + f"$before=[IO.File]::GetLastWriteTimeUtc({_quote(launcher)})\n"
        + "Ensure-ScheduledTask -RefreshLauncher\n"
        + f"if ([IO.File]::GetLastWriteTimeUtc({_quote(launcher)}) -ne $before) "
        + "{ throw 'unchanged launcher was rewritten' }\n",
        encoding="utf-8-sig",
    )
    result = _run(host, publish, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    content = launcher.read_text(encoding="utf-8-sig")
    assert "# obsolete startup" not in content
    assert "Resolve-UvRuntimePython -ExpectedPython $launchPy" in content
    assert not list(root.glob(".start-agent-bridge.ps1.*"))
    assert not list((root / "bin").glob(".*"))


def test_service_publisher_honors_explicit_wheel_uv(tmp_path: Path) -> None:
    host = shutil.which("pwsh")
    uv = shutil.which("uv")
    if not host or not uv:
        pytest.skip("PowerShell Core and prepared UV are required for publication")
    root = tmp_path / "bridge"
    root.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    publish = scripts / "publish.ps1"
    publish.write_text(
        "$WheelProfile='closed'; $WheelUv=" + _quote(uv) + "\n"
        "$env:PATH=''\nfunction Get-Command { throw 'ambient UV lookup forbidden' }\n"
        + _publication_source(root),
        encoding="utf-8-sig",
    )
    result = _run(host, publish, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    launcher = (root / "start-agent-bridge.ps1").read_text(encoding="utf-8-sig")
    assert f"$preparedUv = {_quote(uv)}" in launcher


@pytest.mark.parametrize("operation", ["schedule", "provision"])
def test_service_task_only_operations_retain_startup_without_uv(
    tmp_path: Path, operation: str,
) -> None:
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell Core is required for retained task publication")
    root = tmp_path / "bridge"
    root.mkdir()
    launcher = root / "start-agent-bridge.ps1"
    launcher.write_text("# retained startup binding\n", encoding="utf-8")
    script = tmp_path / "task-only.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir={_quote(root)}; $LinkPython={_quote(sys.executable)}\n"
        "$TaskName='fixture-only'; $ForceTaskRepair=$false; $script:registered=0\n"
        "$env:LOCALAPPDATA=$InstallDir; $env:USERNAME='fixture'; $env:PATH=''\n"
        f"$operation='{operation}'\n"
        "function Write-Ok {}; function Write-Step {}; function Write-Skip {}\n"
        "function Write-Warn { throw $args[0] }\n"
        "function Get-WindowsScheduledTaskPolicy {\n"
        " if ($operation -eq 'schedule') { 'enabled' } else { 'preserve' } }\n"
        "function Get-SchedulingPolicyTask { param($Policy)\n"
        " if ($operation -ne 'schedule') {\n"
        " [pscustomobject]@{Principal=[pscustomobject]@{LogonType='S4U'}} } }\n"
        "function Write-BridgeServiceLauncher { throw 'retained startup binding was republished' }\n"
        "function Get-Command { param($Name,$ErrorAction)\n"
        " if ($Name -eq 'uv') { throw 'task-only UV lookup forbidden' } }\n"
        "function Resolve-DaemonLogonMode { $script:UseNonInteractive=$false }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) }\n"
        "function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) @{} }\n"
        "function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User) [pscustomobject]@{Delay=''} }\n"
        "function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,\n"
        " [switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit,$MultipleInstances) @{} }\n"
        "function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Description,[switch]$Force)\n"
        " $script:registered++ }\n"
        "function Set-ScheduledTask { throw 'existing task mutation forbidden' }\n"
        "function Remove-RegisteredTask { throw 'task removal forbidden' }\n"
        "function Get-PayloadHash { 'fixture' }; function Ensure-RuntimeSlotRetention {}\n"
        "function Enter-InstallLock { $true }; function Exit-InstallLock {}\n"
        + _function("Register-ScheduledTask_")
        + _function("Ensure-ScheduledTask")
        + _function("Invoke-Provision")
        + "\nif ($operation -eq 'schedule') { Ensure-ScheduledTask } else { Invoke-Provision }\n"
        + "[Console]::WriteLine($script:registered)\n",
        encoding="utf-8-sig",
    )
    result = _run(host, script, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ("1" if operation == "schedule" else "0")
    assert launcher.read_text() == "# retained startup binding\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipe readiness for the portable publisher")
def test_service_publisher_keeps_complete_script_until_atomic_replace(tmp_path: Path) -> None:
    host = shutil.which("pwsh")
    if not host or not shutil.which("uv"):
        pytest.skip("PowerShell Core and prepared UV are required for publication")
    root = tmp_path / "bridge"
    root.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    launcher = root / "start-agent-bridge.ps1"
    old = "param([switch]$ResolveOnly)\nWrite-Output 'complete old launcher'\n"
    launcher.write_text(old, encoding="utf-8")
    hook = (
        "if ($Destination -ceq (Join-Path $InstallDir 'start-agent-bridge.ps1')) {\n"
        " [Console]::WriteLine('ready'); [Console]::Out.Flush()\n"
        " if ([Console]::ReadLine() -cne 'commit') { throw 'fixture publication cancelled' }\n"
        "}\n"
    )
    original = "[IO.File]::Replace($temporary, $Destination, [NullString]::Value)"
    publish = scripts / "publish.ps1"
    source = _publication_source(root)
    assert source.count(original) == 1
    publish.write_text(source.replace(original, hook + original), encoding="utf-8-sig")
    with subprocess.Popen(
        [host, "-NoProfile", "-NonInteractive", "-File", str(publish)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 10)[0], "publisher did not reach atomic commit"
        assert process.stdout.readline() == "ready\n"
        assert launcher.read_text() == old
        result = _run(host, launcher, dict(os.environ))
        assert result.returncode == 0 and result.stdout.strip() == "complete old launcher"
        staged = list(root.glob(".start-agent-bridge.ps1.*"))
        assert len(staged) == 1 and "Resolve-UvRuntimePython" in staged[0].read_text("utf-8-sig")
        output, error = process.communicate("commit\n", timeout=15)
        assert process.returncode == 0, output + error
    assert "Resolve-UvRuntimePython" in launcher.read_text("utf-8-sig")
    assert not list(root.glob(".start-agent-bridge.ps1.*"))
