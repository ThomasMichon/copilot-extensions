"""Actual prepared Windows sync publication and execution, without enrollment."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from .managed_sync_fixture_ps1 import compose_sync_scripts

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SUPPORT = ("resolve-runtime.ps1", "bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe")
pytestmark = pytest.mark.portfolio_tier("T3")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _function(name: str) -> str:
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8-sig")
    start = source.index(f"function {name} {{")
    return source[start:source.index("\n}\n", start) + 3]


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class SyncFixture:
    root: Path
    scripts: Path
    publish: Path
    environment: dict[str, str]
    managed_python: Path
    uv: Path


@pytest.fixture(scope="module")
def windows_sync(tmp_path_factory: pytest.TempPathFactory) -> SyncFixture:
    if os.name != "nt":
        pytest.skip("native Windows sync ingress")
    uv = shutil.which("uv")
    assert uv, "explicitly prepare UV before native sync qualification"
    folder = tmp_path_factory.mktemp("logger-sync")
    root = folder / "sync space % dollar$ 'quoted'-\u03bb"
    scripts = compose_sync_scripts(folder / "payload")
    bound_uv = folder / "bound-uv.exe"
    shutil.copyfile(uv, bound_uv)
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX",
        } and not key.upper().startswith(("AGENT_LOGGER_", "COPILOT_PLUGIN_"))
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
        package = slot / "Lib/site-packages/agent_logger"
        (package / "sync").mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "sync/__init__.py").write_text("", encoding="utf-8")
        (package / "sync/engine.py").write_text(
            "import ctypes,json,os,sys\nfrom pathlib import Path\n"
            "Path(os.environ['SYNC_WORKER']).write_text('called')\n"
            "print(json.dumps({'python':sys.executable,'base':sys._base_executable,\n"
            " 'prefix':sys.prefix,'argv':sys.argv[1:],'cwd':os.getcwd(),\n"
            " 'home':os.environ.get('AGENT_LOGGER_HOME'),'isolated':sys.flags.isolated,\n"
            " 'utf8':sys.flags.utf8_mode,'console':bool(ctypes.windll.kernel32.GetConsoleWindow()),\n"
            " 'pythonhome':os.environ.get('PYTHONHOME'),'pythonpath':os.environ.get('PYTHONPATH')}))\n"
            "print('sync worker stderr',file=sys.stderr)\nsys.exit(23)\n", encoding="utf-8",
        )
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 0,
        }), encoding="utf-8")
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    publish = scripts / "publish.ps1"
    publish.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={_quote(root)}; $TaskLauncher=Join-Path $InstallDir 'bin/session-sync-task.ps1'\n"
        f"$ScriptDir={_quote(scripts)}; $WheelProfile='fixture'; $WheelUv={_quote(bound_uv)}\n"
        "function Get-ConfigRepoRegistrationPath {}\n"
        "function New-ScheduledTaskAction { param($Execute,$Argument); "
        "[pscustomobject]@{Execute=$Execute;Arguments=$Argument} }\n"
        + _function("Write-SyncTaskLauncher")
        + _function("New-SyncTaskAction") + "\nNew-SyncTaskAction | ConvertTo-Json -Compress\n",
        encoding="utf-8-sig",
    )
    manager = folder / "no-python-no-uv"
    manager.mkdir()
    environment.update(
        SYNC_WORKER=str(root / "worker-called"), SYNC_POISON=str(root / "ambient-called"),
        SYNC_MANAGER_PATH=str(manager),
    )
    for name in ("uv", "python"):
        (manager / f"{name}.cmd").write_text(
            '@echo off\r\n> "%SYNC_POISON%" echo called\r\nexit /b 94\r\n', encoding="ascii",
        )
    return SyncFixture(root, scripts, publish, environment, Path(prepared.stdout.strip()), bound_uv)


@pytest.fixture(params=("powershell", "pwsh"))
def sync_shell(request: pytest.FixtureRequest) -> str:
    if os.name != "nt":
        pytest.skip("native Windows sync ingress")
    host = shutil.which(str(request.param))
    assert host, f"native {request.param} is required"
    return host


def _run(host: str, script: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=NO_WINDOW,
    )


def _publish(fixture: SyncFixture, host: str) -> Path:
    result = _run(host, fixture.publish, fixture.environment)
    assert result.returncode == 0, result.stderr
    action = json.loads(result.stdout)
    assert action["Execute"] == str(fixture.root / "bin/bootstrap-capture.exe")
    assert action["Arguments"].startswith('--task 1515000 15000 "')
    assert action["Arguments"].endswith('-File "' + str(fixture.root / "bin/session-sync-task.ps1") + '"')
    for name in SUPPORT:
        assert (fixture.root / "bin" / name).read_bytes() == (fixture.scripts / name).read_bytes()
    return fixture.root / "bin/session-sync-task.ps1"


def test_windows_sync_uses_managed_runtime_through_gui_task(
    windows_sync: SyncFixture, sync_shell: str, tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    fixture = windows_sync
    launcher = _publish(fixture, sync_shell)
    environment = dict(
        fixture.environment, PATH=fixture.environment["SYNC_MANAGER_PATH"],
        PYTHONHOME="unusable-python-home", PYTHONPATH="unusable-python-path",
        UV_PYTHON="unusable-selection", VIRTUAL_ENV="unusable-venv",
        UV_CONFIG_FILE="unusable-configuration",
    )
    log = fixture.root / "logs/session-sync.log"
    calls = tmp_path / "uv-calls.jsonl"
    environment["SYNC_UV_CALLS"] = str(calls)
    capture = fixture.root / "bin/bootstrap-capture.ps1"
    original = capture.read_bytes()
    capture.write_text(capture.read_text(encoding="utf-8-sig") + """
Copy-Item Function:\\Invoke-BootstrapOwnedCapture Function:\\Invoke-FixtureOwnedCapture
function Invoke-BootstrapOwnedCapture {
    param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment,
        [int]$TimeoutMilliseconds, [int]$CleanupMilliseconds)
    [IO.File]::AppendAllText($env:SYNC_UV_CALLS,
        ((@{file=$FilePath; arguments=$ArgList} | ConvertTo-Json -Compress) + [Environment]::NewLine))
    Invoke-FixtureOwnedCapture @PSBoundParameters
}
""", encoding="utf-8-sig")
    samples = []
    try:
        for version in ("1.0.0", "2.0.0"):
            (fixture.root / "current-version").write_text(version, encoding="utf-8")
            started = time.monotonic()
            result = subprocess.run(
                [str(fixture.root / "bin/bootstrap-capture.exe"), "--task", "25000", "1000",
                 str(log), str(fixture.root), sync_shell, "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(launcher)],
                env=environment, capture_output=True, text=True, timeout=28, creationflags=NO_WINDOW,
            )
            samples.append(time.monotonic() - started)
            output = log.read_text(encoding="utf-8")
            assert result.returncode == 23, output + result.stderr
            observed = next(json.loads(line) for line in output.splitlines() if line.startswith("{"))
            slot = fixture.root / "versions" / version
            assert Path(observed["python"]).samefile(slot / "Scripts/python.exe")
            assert Path(observed["base"]).samefile(fixture.managed_python)
            assert observed["cwd"] == observed["home"] == str(fixture.root)
            assert observed["argv"] == ["run", "--prune"]
            assert observed["utf8"] == observed["isolated"] == 1
            assert observed["console"] is False
            assert observed["pythonhome"] is None and observed["pythonpath"] is None
            assert "sync worker stderr" in output and not result.stdout
            assert not Path(environment["SYNC_POISON"]).exists()
            invocations = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            assert len(invocations) == len(samples)
            assert Path(invocations[-1]["file"]).samefile(fixture.uv)
            assert invocations[-1]["arguments"] == [
                "python", "find", "--no-project", "--managed-python", "--no-python-downloads",
                str(slot / "Scripts/python.exe"),
            ]
    finally:
        capture.write_bytes(original)
    record_property("logger_windows_sync_ingress", json.dumps({
        "shell": Path(sync_shell).stem.lower(), "cycles": 2, "samples_seconds": samples,
        "parent": "physical-gui", "runtime": "managed-venv", "network": "no-downloads",
        "tail_qualification": "insufficient samples",
    }))


def test_windows_sync_publication_is_atomic_and_idempotent(
    windows_sync: SyncFixture, sync_shell: str,
) -> None:
    fixture = windows_sync
    launcher = _publish(fixture, sync_shell)
    paths = [launcher, *(fixture.root / "bin" / name for name in SUPPORT)]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    _publish(fixture, sync_shell)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    source = fixture.scripts / "bootstrap-capture.exe"
    held = source.with_suffix(".held")
    source.rename(held)
    try:
        result = _run(sync_shell, fixture.publish, fixture.environment)
        assert result.returncode != 0
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
        assert not list((fixture.root / "bin").glob(".*"))
    finally:
        held.rename(source)


@pytest.mark.parametrize("missing", ("uv", "runtime", "resolve-runtime.ps1", "bootstrap-uv.ps1", "bootstrap-capture.exe"))
def test_windows_sync_missing_preparation_refuses_before_application(
    windows_sync: SyncFixture, sync_shell: str, missing: str,
) -> None:
    fixture = windows_sync
    launcher = _publish(fixture, sync_shell)
    (fixture.root / "current-version").write_text("1.0.0", encoding="utf-8")
    path = {"uv": fixture.uv, "runtime": fixture.root / "versions"}.get(
        missing, fixture.root / "bin" / missing,
    )
    held = path.with_name(path.name + ".held")
    witness = Path(fixture.environment["SYNC_WORKER"])
    witness.unlink(missing_ok=True)
    path.rename(held)
    try:
        result = _run(sync_shell, launcher, dict(
            fixture.environment, PATH=fixture.environment["SYNC_MANAGER_PATH"],
        ))
        assert result.returncode != 0, result.stdout + result.stderr
        assert not witness.exists() and not result.stdout.strip()
        assert not Path(fixture.environment["SYNC_POISON"]).exists()
    finally:
        held.rename(path)
