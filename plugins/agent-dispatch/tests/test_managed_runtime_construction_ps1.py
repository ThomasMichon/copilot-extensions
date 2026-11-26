"""PowerShell construction preserves explicit bases and managed default custody."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from . import source_publication_fixture as fixture
from .test_managed_bootstrap_reader import prepared_reader_payload as prepared_reader_payload


@pytest.fixture(scope="module")
def prepared_construction_python(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires existing uv")
    selected = subprocess.run(
        [uv, "--no-config", "python", "find", "--no-project", "--system",
         "--managed-python", "--no-python-downloads", "3.11"],
        capture_output=True, text=True, timeout=15, creationflags=fixture.HEADLESS,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared managed Python 3.11")
    base = Path(selected.stdout.strip()).resolve()
    installation = base.parent if os.name == "nt" else base.parent.parent
    root = tmp_path_factory.mktemp("dispatch-construction-python")
    copied = root / installation.name
    shutil.copytree(installation, copied, symlinks=True)
    return Path(uv), root, copied / base.relative_to(installation)


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("scenario", [
    "raw", "packaged", "partial", "unprepared", "uv-failure", "explicit",
])
def test_powershell_construction_uses_managed_default_without_ambient_python(
    tmp_path: Path, shell: str, scenario: str, prepared_reader_payload: Path,
    prepared_construction_python: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    host = shutil.which(shell)
    if not host or shell == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native PowerShell host")
    uv, managed_root, base = prepared_construction_python
    scripts = prepared_reader_payload / "scripts"
    engine = (
        fixture.REPO / "libs/installer-engine/installer-engine.ps1" if scenario == "raw"
        else scripts / "installer-engine.ps1"
    )
    env = fixture.environment(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        command = commands / (name + (".cmd" if os.name == "nt" else ""))
        command.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\nexit /b 91\n'
            if os.name == "nt" else '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    runtime = tmp_path / "candidate"
    runtime.mkdir()
    receipt = runtime / ".construction-receipt.json"
    receipt.write_text('{"owner":"existing-construction"}\n', encoding="utf-8")
    runtime_python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if scenario == "partial":
        runtime_python.parent.mkdir()
        runtime_python.write_bytes(b"partial")
    empty_root = tmp_path / "unprepared-python"
    empty_root.mkdir()
    (tmp_path / ".python-version").write_text("3.9\n", encoding="utf-8")
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(empty_root if scenario == "unprepared" else managed_root),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_CONFIG_FILE="missing-relative.toml",
        UV_NO_CONFIG="false", UV_PYTHON=str(commands / "python"),
        UV_NO_MANAGED_PYTHON="1", VIRTUAL_ENV=str(runtime), CONDA_PREFIX=str(runtime),
        PYTHONHOME=str(tmp_path / "foreign-python"), PYTHONPATH=str(tmp_path / "foreign-modules"),
    )
    runner = tmp_path / "construct.ps1"
    calls = tmp_path / "uv-calls.jsonl"
    runner.write_text(
        "param($Engine,$Native,$Runtime,$Python,$SelectedUv,$SelectedBase,$Scenario,$Calls)\n"
        "$ErrorActionPreference='Stop'\n. $Engine\n. $Native\n"
        + fixture.function("ps1", "Test-DispatchVenv") + "\n"
        + fixture.function("ps1", "New-DispatchRuntimeVenv") + "\n"
        "$VenvDir=$Runtime; $VenvPython=$Python\n"
        "$originalCapture=${function:Invoke-Hidden}\n"
        "function Invoke-Hidden {\n"
        " param($FilePath,$ArgList,[ref]$ErrorOutput,$Environment)\n"
        " if ($FilePath -eq $SelectedUv) {\n"
        "  [IO.File]::AppendAllText($Calls, (ConvertTo-Json -Compress -InputObject @($ArgList)) + \"`n\")\n"
        "  if ($Scenario -eq 'uv-failure') { $ErrorOutput.Value='controlled failure'; $global:LASTEXITCODE=19; return '' }\n"
        " }\n"
        " $output=& $originalCapture @PSBoundParameters\n"
        " $code=$LASTEXITCODE; $global:LASTEXITCODE=$code; return $output\n"
        "}\n"
        "function Write-Warn { param($Message) [Console]::Error.WriteLine($Message) }\n"
        "$before=@{}\n"
        "foreach ($name in @('PATH','UV_CONFIG_FILE','UV_NO_CONFIG','UV_PYTHON',"
        "'UV_NO_MANAGED_PYTHON','VIRTUAL_ENV','CONDA_PREFIX','PYTHONHOME','PYTHONPATH')) {\n"
        " $before[$name]=[Environment]::GetEnvironmentVariable($name)\n}\n"
        "$binding=if ($Scenario -eq 'explicit') { $SelectedBase } else { '' }\n"
        "try { New-DispatchRuntimeVenv -UvCommand $SelectedUv -BasePython $binding } catch {\n"
        " [Console]::Error.WriteLine($_.Exception.Message); exit 1\n}\n"
        "foreach ($name in $before.Keys) {\n"
        " if ([Environment]::GetEnvironmentVariable($name) -cne $before[$name]) { throw \"Changed caller: $name\" }\n}\n"
        "$output=Invoke-Hidden -FilePath $Python -ArgList @('-I','-B','-c',"
        "'import json,sys; print(json.dumps(dict(version=list(sys.version_info[:2]),base=sys.base_prefix)))')\n"
        "if ($LASTEXITCODE -ne 0) { throw 'Constructed interpreter probe failed' }\n"
        "$output\n",
        encoding="utf-8",
    )
    success = scenario in {"raw", "packaged", "partial", "explicit"}
    expected = 2 if success else 1
    samples = []
    for _ in range(expected):
        started = time.monotonic()
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(runner), str(engine), str(scripts / "versioned_runtime_native.ps1"),
             str(runtime), str(runtime_python), str(uv), str(base), scenario, str(calls)],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=30, creationflags=fixture.HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not marker.exists(), result.stdout + result.stderr
        assert receipt.read_text(encoding="utf-8") == '{"owner":"existing-construction"}\n'
        if success:
            assert result.returncode == 0, result.stdout + result.stderr
            identity = json.loads(result.stdout.strip())
            assert identity["version"] == [3, 11]
            assert Path(identity["base"]).resolve().is_relative_to(managed_root)
            if scenario == "explicit":
                installation = base.parent if os.name == "nt" else base.parent.parent
                assert os.path.samefile(identity["base"], installation)
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            assert not (runtime / "pyvenv.cfg").exists()
            if scenario == "uv-failure":
                assert "19" in result.stderr
    uv_calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    assert all(call[0] in {"python", "venv"} for call in uv_calls), uv_calls
    assert all(
        "--managed-python" in call and "--no-project" in call for call in uv_calls
    ), uv_calls
    if scenario == "explicit":
        assert not uv_calls
    else:
        assert len(uv_calls) >= expected
    record_property("dispatch_powershell_construction_performance", json.dumps({
        "status": "measured", "scenario": scenario, "shell": shell, "expected": expected,
        "completed": len(samples), "samples_seconds": samples, "uv_calls": uv_calls,
        "network": "disabled", "runtime_python": "3.11",
        "cache": "fresh/retained private UV cache; prepared Python; OS caches uncontrolled",
        "unavailable": ["child CPU", "full stamp/install latency", "tail qualification"],
    }))


@pytest.mark.parametrize("installed", [True, False])
def test_powershell_construction_retries_untrusted_minor_link_with_exact_patch(
    tmp_path: Path, installed: bool,
) -> None:
    """Dev Box RedirectionGuard refuses uv's 3.11 junction (os error 448);
    construction retries once with the installed exact managed patch."""
    host = shutil.which("pwsh") or shutil.which("powershell")
    if not host:
        pytest.skip("This control requires a PowerShell host")
    listing = [
        {"version": version, "implementation": "cpython", "variant": "default", "path": None,
         "version_parts": dict(zip(("major", "minor", "patch"), map(int, version.split("."))))}
        for version in ("3.11.19", "3.11.18", "3.11.7", "3.12.3")
    ]
    installed_patches = "3.11.7,3.11.18,3.12.3" if installed else "3.12.3"
    bootstrap = tmp_path / "policy"
    bootstrap.mkdir()
    (bootstrap / "bootstrap-uv.ps1").write_text(
        "function Get-UvBootstrapEnvironment { return @{} }\n"
        "function Get-InstallerManagedSignedBasePython { return $null }\n"
        "function Invoke-UvVenvResilient {\n"
        " param($VenvDir,$UvCommand,$Arguments)\n"
        " $output = & $UvCommand venv $VenvDir @Arguments\n"
        " return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }\n}\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "candidate"
    calls = tmp_path / "uv-calls.jsonl"
    runner = tmp_path / "construct.ps1"
    runner.write_text(
        "param($Engine,$Policy,$Runtime,$Listing,$Installed,$Calls)\n"
        "$ErrorActionPreference='Stop'\n. $Engine\n$env:OS='Windows_NT'\n"
        + fixture.function("ps1", "Test-DispatchVenv") + "\n"
        + fixture.function("ps1", "New-DispatchRuntimeVenv") + "\n"
        "$script:InstallerDependencyPolicy=$Policy\n"
        "$VenvDir=$Runtime; $VenvPython=Join-Path $Runtime 'Scripts/python.exe'\n"
        "function Test-DispatchVenv { param($Dir,$Python) Test-Path -LiteralPath $Python }\n"
        "function Write-Warn { param($Message) [Console]::Error.WriteLine($Message) }\n"
        "function Invoke-Hidden {\n"
        " param($FilePath,$ArgList,[ref]$ErrorOutput,$Environment)\n"
        " [IO.File]::AppendAllText($Calls, (ConvertTo-Json -Compress -InputObject @($ArgList)) + \"`n\")\n"
        " if ($ArgList[0] -eq 'python' -and $ArgList[1] -eq 'list') {\n"
        "  $ErrorOutput.Value='warning: fixture diagnostic'; $global:LASTEXITCODE=0; return $Listing\n }\n"
        " if ($ArgList[0] -eq 'python' -and $ArgList[1] -eq 'find') {\n"
        "  $global:LASTEXITCODE=$(if (($Installed -split ',') -contains $ArgList[-1]) { 0 } else { 2 }); return ''\n }\n"
        " if ($ArgList[$ArgList.IndexOf('--python')+1] -eq '3.11') {\n"
        "  $ErrorOutput.Value='error: untrusted mount point. (os error 448)'; $global:LASTEXITCODE=2; return ''\n }\n"
        " $null=New-Item -ItemType Directory -Force (Join-Path $ArgList[1] 'Scripts')\n"
        " Set-Content -LiteralPath (Join-Path $ArgList[1] 'Scripts/python.exe') -Value fixture\n"
        " $global:LASTEXITCODE=0; return ''\n}\n"
        "try { New-DispatchRuntimeVenv -UvCommand 'uv-fixture' } catch {\n"
        " [Console]::Error.WriteLine($_.Exception.Message); exit 1\n}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner),
         str(fixture.REPO / "libs/installer-engine/installer-engine.ps1"),
         str(bootstrap / "installer-dependencies.json"), str(runtime), json.dumps(listing), installed_patches, str(calls)],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, creationflags=fixture.HEADLESS,
    )
    pythons = [
        args[args.index("--python") + 1]
        for args in map(json.loads, calls.read_text(encoding="utf-8").splitlines())
        if "--python" in args
    ]
    if installed:
        assert result.returncode == 0, result.stdout + result.stderr
        assert pythons == ["3.11", "3.11.18"]
        assert "retrying with the installed 3.11.18 interpreter" in result.stderr
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert pythons == ["3.11"]
