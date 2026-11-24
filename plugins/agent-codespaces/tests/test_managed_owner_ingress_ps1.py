"""Qualify the installed Windows owner entry without touching ScheduledTasks."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SUPPORT = ("resolve-runtime.ps1", "bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe")
pytestmark = pytest.mark.portfolio_tier("T3")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _function(name: str, file: str = "install.ps1") -> str:
    source = (SCRIPTS / file).read_text(encoding="utf-8-sig")
    start = source.index(f"function {name} {{")
    return source[start:source.index("\n}\n", start) + 3]


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class OwnerFixture:
    root: Path
    scripts: Path
    publish: Path
    environment: dict[str, str]
    managed_python: Path
    uv: Path


@pytest.fixture(scope="module")
def windows_owner(tmp_path_factory: pytest.TempPathFactory) -> OwnerFixture:
    if os.name != "nt":
        pytest.skip("native Windows owner ingress")
    uv = shutil.which("uv")
    assert uv, "explicitly prepare UV before the native owner controls"
    folder = tmp_path_factory.mktemp("codespaces-owner")
    root = folder / "owner space % dollar$ 'quoted'-\u03bb"
    scripts = folder / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in (*SUPPORT, "codespaces-owner.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    bound_uv = folder / "bound-uv.exe"
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
        package = slot / "Lib/site-packages/agent_codespaces"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "connection_owner.py").write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            "def ensure_owner_running(config):\n"
            " Path(os.environ['OWNER_WORKER']).write_text(json.dumps({\n"
            "  'python':sys.executable,'config_is_none':config is None,'isolated':sys.flags.isolated}))\n"
            " return True\n", encoding="utf-8",
        )
        (package / "__main__.py").write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            "Path(os.environ['OWNER_WORKER']).write_text('called')\n"
            "if sys.argv[1:]==['owner','--status']:\n"
            " print(json.dumps({'enabled':True,'reconcile_interval':17}));sys.exit(0)\n"
            "print(json.dumps({'python':sys.executable,'base':sys._base_executable,\n"
            " 'prefix':sys.prefix,'pid':os.getpid(),'parent':os.getppid(),\n"
            " 'argv':sys.argv[1:],'cwd':os.getcwd(),'stdin':sys.stdin.read(),\n"
            " 'home':os.environ.get('AGENT_CODESPACES_HOME'),\n"
            " 'pythonhome':os.environ.get('PYTHONHOME'),'pythonpath':os.environ.get('PYTHONPATH')}))\n"
            "print('owner worker stderr',file=sys.stderr)\nsys.exit(23)\n", encoding="utf-8",
        )
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 0,
        }), encoding="utf-8")
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    publish = scripts / "publish.ps1"
    publish.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={_quote(root)}; $WheelProfile='fixture'; $WheelUv={_quote(bound_uv)}\n"
        + _function("Publish-RuntimeHelperFile")
        + _function("Publish-ConnectionOwnerEntry")
        + "\nPublish-ConnectionOwnerEntry\n", encoding="utf-8-sig",
    )
    manager = folder / "no-python-no-uv"
    manager.mkdir()
    environment.update(
        OWNER_WORKER=str(root / "worker-called"), OWNER_POISON=str(root / "ambient-called"),
        OWNER_MANAGER_PATH=str(manager),
    )
    for name in ("uv", "python"):
        (manager / f"{name}.cmd").write_text(
            '@echo off\r\n> "%OWNER_POISON%" echo called\r\nexit /b 94\r\n', encoding="ascii",
        )
    return OwnerFixture(root, scripts, publish, environment, Path(prepared.stdout.strip()), bound_uv)


@pytest.fixture(params=("powershell", "pwsh"))
def owner_shell(request: pytest.FixtureRequest) -> str:
    if os.name != "nt":
        pytest.skip("native Windows owner ingress")
    host = shutil.which(str(request.param))
    assert host, f"native {request.param} is required"
    return host


def _run(host: str, script: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=NO_WINDOW,
    )


def _publish(fixture: OwnerFixture, host: str) -> Path:
    result = _run(host, fixture.publish, fixture.environment)
    assert result.returncode == 0, result.stderr
    for name in SUPPORT:
        assert (fixture.root / "bin" / name).read_bytes() == (fixture.scripts / name).read_bytes()
    return fixture.root / "bin/codespaces-owner.ps1"


def test_windows_owner_keeps_managed_selection_streams_and_cutover(
    windows_owner: OwnerFixture, owner_shell: str, tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    fixture = windows_owner
    launcher = _publish(fixture, owner_shell)
    environment = dict(
        fixture.environment, PATH=fixture.environment["OWNER_MANAGER_PATH"],
        PYTHONHOME="unusable-python-home", PYTHONPATH="unusable-python-path",
        UV_PYTHON="unusable-python-selection", VIRTUAL_ENV="unusable-venv",
        UV_CONFIG_FILE="unusable-uv-configuration",
    )
    capture = fixture.root / "bin/bootstrap-capture.ps1"
    original = capture.read_bytes()
    calls = tmp_path / "uv-calls.jsonl"
    environment["OWNER_UV_CALLS"] = str(calls)
    capture.write_text(capture.read_text(encoding="utf-8-sig") + """
Copy-Item Function:\\Invoke-BootstrapOwnedCapture Function:\\Invoke-FixtureOwnedCapture
function Invoke-BootstrapOwnedCapture {
    param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment,
        [int]$TimeoutMilliseconds, [int]$CleanupMilliseconds)
    [IO.File]::AppendAllText($env:OWNER_UV_CALLS,
        ((@{file=$FilePath; arguments=$ArgList} | ConvertTo-Json -Compress) + [Environment]::NewLine))
    Invoke-FixtureOwnedCapture @PSBoundParameters
}
""", encoding="utf-8-sig")
    runner = tmp_path / "run.ps1"
    runner.write_text(
        f"& {_quote(launcher)} owner 'two words' '' 'quote\"literal' 'dollar$; & literal'\n"
        "exit $LASTEXITCODE\n", encoding="utf-8-sig",
    )
    samples = []
    try:
        for version in ("1.0.0", "2.0.0", "1.0.0", "2.0.0"):
            (fixture.root / "current-version").write_text(version, encoding="utf-8")
            started = time.monotonic()
            result = subprocess.run(
                [owner_shell, "-NoProfile", "-NonInteractive", "-File", str(runner)],
                env=environment, input="owner stdin \u03bb\n", capture_output=True,
                text=True, encoding="utf-8", timeout=20, creationflags=NO_WINDOW,
            )
            samples.append(time.monotonic() - started)
            assert result.returncode == 23, result.stderr
            observed = json.loads(result.stdout)
            slot = fixture.root / "versions" / version
            assert Path(observed["python"]).samefile(slot / "Scripts/python.exe")
            assert Path(observed["base"]).samefile(fixture.managed_python)
            assert Path(observed["prefix"]).samefile(slot)
            assert observed["cwd"] == observed["home"] == str(fixture.root)
            assert observed["stdin"] == "owner stdin \u03bb\n"
            assert observed["argv"] == ["owner", "two words", "", 'quote"literal', "dollar$; & literal"]
            assert observed["pythonhome"] is None and observed["pythonpath"] is None
            assert "owner worker stderr" in result.stderr
            assert not Path(environment["OWNER_POISON"]).exists()
            invocations = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            assert len(invocations) == len(samples)
            assert Path(invocations[-1]["file"]).samefile(fixture.uv)
            assert invocations[-1]["arguments"] == [
                "python", "find", "--no-project", "--managed-python",
                "--no-python-downloads", str(slot / "Scripts/python.exe"),
            ]
        record_property("codespaces_windows_owner_ingress", json.dumps({
            "status": "measured", "shell": Path(owner_shell).stem.lower(),
            "expected": 4, "completed": len(samples), "samples_seconds": samples,
            "phases": ["cold", "cold", "warm", "warm"], "selector_calls": len(invocations),
            "network": "no-downloads", "tail_qualification": "insufficient samples",
        }))
    finally:
        capture.write_bytes(original)


@pytest.mark.parametrize("missing", ("uv", "runtime", "resolve-runtime.ps1", "bootstrap-uv.ps1", "bootstrap-capture.exe"))
def test_windows_owner_missing_preparation_refuses_before_application(
    windows_owner: OwnerFixture, owner_shell: str, missing: str,
) -> None:
    fixture = windows_owner
    launcher = _publish(fixture, owner_shell)
    (fixture.root / "current-version").write_text("1.0.0", encoding="utf-8")
    paths = {"uv": fixture.uv, "runtime": fixture.root / "versions"}
    path = paths.get(missing, fixture.root / "bin" / missing)
    held = path.with_name(path.name + ".held")
    witness = Path(fixture.environment["OWNER_WORKER"])
    witness.unlink(missing_ok=True)
    path.rename(held)
    try:
        result = _run(owner_shell, launcher, dict(
            fixture.environment, PATH=fixture.environment["OWNER_MANAGER_PATH"],
        ))
        assert result.returncode != 0, result.stdout + result.stderr
        assert not witness.exists() and not result.stdout.strip()
        assert not Path(fixture.environment["OWNER_POISON"]).exists()
    finally:
        held.rename(path)


def test_windows_owner_publication_is_atomic_and_idempotent(
    windows_owner: OwnerFixture, owner_shell: str,
) -> None:
    fixture = windows_owner
    launcher = _publish(fixture, owner_shell)
    paths = [launcher, *(fixture.root / "bin" / name for name in SUPPORT)]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    _publish(fixture, owner_shell)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    source = fixture.scripts / "bootstrap-capture.exe"
    held = source.with_suffix(".held")
    source.rename(held)
    try:
        result = _run(owner_shell, fixture.publish, fixture.environment)
        assert result.returncode != 0
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
        assert not list((fixture.root / "bin").glob(".*"))
    finally:
        held.rename(source)


def test_windows_owner_status_and_selected_ensure_use_the_entry(
    windows_owner: OwnerFixture, owner_shell: str, tmp_path: Path,
) -> None:
    fixture = windows_owner
    launcher = _publish(fixture, owner_shell)
    (fixture.root / "current-version").write_text("1.0.0", encoding="utf-8")
    runner = tmp_path / "query.ps1"
    runner.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={_quote(fixture.root)}\n"
        + _function("Get-ConnectionOwnerConfig")
        + "\n$config=Get-ConnectionOwnerConfig\n"
        "if (-not $config.Known -or -not $config.Enabled -or $config.Interval -ne 17) { throw 'Wrong owner configuration' }\n"
        f"& {_quote(launcher)} -EnsureSelected\nexit $LASTEXITCODE\n", encoding="utf-8-sig",
    )
    result = _run(owner_shell, runner, dict(
        fixture.environment, PATH=fixture.environment["OWNER_MANAGER_PATH"],
    ))
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(Path(fixture.environment["OWNER_WORKER"]).read_text())
    assert observed == {
        "python": str(fixture.root / "versions/1.0.0/Scripts/python.exe"),
        "config_is_none": True, "isolated": 1,
    }


@pytest.mark.parametrize("case", ("held", "unknown", "disabled", "preserve", "absent", "enabled"))
def test_owner_entry_preserves_scheduling_and_capability_holds(tmp_path: Path, case: str) -> None:
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell Core is unavailable")
    root = tmp_path / "installed"
    root.mkdir()
    runner = tmp_path / "policy.ps1"
    runner.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={_quote(root)}; $LocalBin='must-not-use-legacy-bin'; $OwnerTaskName='fixture-owner'\n"
        f"$case='{case}'; $global:published=0; $global:registered=0; $global:ensured=0\n"
        "function Write-ServiceOk { param($Message) }\n"
        "function Write-ServiceWarn { param($Message) }\n"
        "function Write-ServiceChanged { param($Message) }\n"
        "function Get-WindowsScheduledTaskPolicy { if ($case -in @('absent','enabled')) { $case } else { 'preserve' } }\n"
        "function Get-ScheduledTask { }\n"
        "function Get-OwnerScheduledTask { if ($case -eq 'held') { [pscustomobject]@{State='Disabled';Settings=[pscustomobject]@{Enabled=$false}} } }\n"
        "function Assert-OwnerScheduledTaskOwned { param($Task) throw 'unexpected incumbent' }\n"
        "function Remove-OwnerScheduledTaskOnly { }\n"
        "function Publish-ConnectionOwnerEntry {\n"
        " $global:published++\n"
        " $bin=Join-Path $InstallDir 'bin'; [void][IO.Directory]::CreateDirectory($bin)\n"
        " [IO.File]::WriteAllText((Join-Path $bin 'codespaces-owner.ps1'),\n"
        "  'param([switch]$EnsureSelected); if ($EnsureSelected) { $global:ensured++; $global:LASTEXITCODE=0; return }; ' +\n"
        "  'if ($case -eq \"unknown\") { $global:LASTEXITCODE=9; return }; ' +\n"
        "  '$global:LASTEXITCODE=0; @{enabled=($case -ne \"disabled\");reconcile_interval=15} | ConvertTo-Json -Compress')\n"
        "}\n"
        "function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory)\n"
        " if ($Argument -notlike ('*'+(Join-Path $InstallDir 'bin/codespaces-owner.ps1')+'*')) { throw 'legacy task entry' }\n"
        " [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }\n"
        "function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User) }\n"
        "function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) }\n"
        "function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,\n"
        " [switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit,$RestartCount,$RestartInterval) }\n"
        "function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Principal,$Settings,[switch]$Force,$ErrorAction) $global:registered++ }\n"
        + _function("Get-ConnectionOwnerConfig")
        + _function("Sync-ConnectionOwnerService")
        + "\ntry { Sync-ConnectionOwnerService; if ($case -eq 'unknown') { throw 'unexpected success' } }\n"
        "catch { if ($case -ne 'unknown' -or $_ -notmatch 'selected Owner readiness not confirmed') { throw } }\n"
        "$expectedEnsure=[int]($case -in @('preserve','absent','enabled'))\n"
        "if ($global:ensured -ne $expectedEnsure -or $global:published -ne [int]($case -ne 'held') -or\n"
        " $global:registered -ne [int]($case -eq 'enabled')) { throw \"Wrong effects $global:published/$global:registered/$global:ensured\" }\n",
        encoding="utf-8-sig",
    )
    result = _run(host, runner, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("entry", ("legacy", "managed", "foreign"))
def test_owner_task_ownership_accepts_both_owned_entry_forms(tmp_path: Path, entry: str) -> None:
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell Core is unavailable")
    runner = tmp_path / "ownership.ps1"
    runner.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={_quote(tmp_path)}; $LocalBin=Join-Path $InstallDir 'legacy-bin'\n"
        f"$entry='{entry}'\n"
        "$stub=switch ($entry) { 'legacy' { Join-Path $LocalBin 'agent-codespaces.ps1' }\n"
        " 'managed' { Join-Path $InstallDir 'bin/codespaces-owner.ps1' }\n"
        " default { Join-Path $InstallDir 'foreign.ps1' } }\n"
        "$arguments='--headless \"C:\\shell\\pwsh.exe\" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"'+$stub+'\" owner'\n"
        "$task=[pscustomobject]@{State='Ready';Settings=[pscustomobject]@{Enabled=$true};\n"
        " Actions=@([pscustomobject]@{Execute='conhost.exe';Arguments=$arguments;WorkingDirectory=$InstallDir})}\n"
        + _function("Assert-OwnerScheduledTaskOwned", "windows-scheduled-task.ps1")
        + "\ntry { Assert-OwnerScheduledTaskOwned $task; if ($entry -eq 'foreign') { throw 'unexpected acceptance' } }\n"
        "catch { if ($entry -ne 'foreign' -or $_ -notmatch 'foreign or unsupported') { throw } }\n",
        encoding="utf-8-sig",
    )
    result = _run(host, runner, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
